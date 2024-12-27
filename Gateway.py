import logging
import traceback
from flask import Flask, request, jsonify, session, redirect, url_for, Response, Blueprint
from flask_sqlalchemy import SQLAlchemy
import requests
import os
from datetime import datetime, timedelta
from functools import wraps
from flask_cors import CORS
from sqlalchemy import text
import secrets
import hashlib


#이 앱은 cloud GPU 용입니다

# Set up logging
log_file_path = os.path.join(os.getcwd(), 'app.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Internal service URLs (not exposed to clients)
INTERNAL_SERVICES = {
    'uv': '파일위치',  # Changed back to original IP
    'ocr': '파일위치',  # NGINX route for OCR
    'face_recognition': '파일위치'  # Updated to port 5003
}

app = Flask(__name__)
SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev_secret_key'
app.secret_key = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://SQL연결/db_deepid'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_DOMAIN'] = '세션아이피'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Set max file size to 16MB
db = SQLAlchemy(app)

# CORS configuration
CORS(app, resources={r"/*": {
    "origins": ["http://아이피", "http://localhost:3000"],  # Add your frontend domains
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"],
    "supports_credentials": True,
    "expose_headers": ["Content-Type", "Set-Cookie"],
    "max_age": 3600
}})

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            logger.warning("로그인 필요: 접근 거부")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# Admin authentication decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            # Get session ID from cookie
            session_id = request.cookies.get('session')
            if not session_id:
                logger.error("No session cookie found")
                return jsonify({'message': 'Authentication required'}), 401
            
            # Verify session in database
            session_query = text("""
                SELECT s.user_id, s.level 
                FROM staff s
                JOIN login_history lh ON s.user_id = lh.user_id
                WHERE lh.session_id = :session_id
                AND s.is_use = 'Y'
                AND lh.status = 1
                ORDER BY lh.login_time DESC
                LIMIT 1
            """)
            
            result = db.session.execute(session_query, {'session_id': session_id}).first()
            
            if not result:
                logger.error(f"Invalid session: {session_id}")
                return jsonify({'message': 'Invalid session'}), 401
            
            if result.level != 4:
                logger.error(f"Insufficient privileges: {result.user_id} (level {result.level})")
                return jsonify({'message': 'Admin privileges required'}), 403
            
            # Add user info to request context
            g.user_id = result.user_id
            g.user_level = result.level
            
            return f(*args, **kwargs)
            
        except Exception as e:
            logger.error(f"Error in admin_required: {str(e)}")
            return jsonify({'message': 'Authentication error'}), 401
            
    return decorated_function

@app.route('/api/login', methods=['POST'])
def login_api():
    try:
        data = request.json
        if not data:
            logger.error("No data received")
            return jsonify({'message': 'No data received'}), 400
            
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            logger.error("Missing username or password")
            return jsonify({'message': 'Username and password required'}), 400
        
        logger.info(f"Login attempt - Username: {username}")
        
        # Query staff table
        query = text("""
            SELECT * FROM staff 
            WHERE user_id = :username 
            AND passwd = :password 
            AND is_use = 'Y'
        """)
        
        result = db.session.execute(query, {
            'username': username,
            'password': password
        }).fetchone()

        if not result:
            logger.warning(f"로그인 실패: 사용자 이름: {username}")
            return jsonify({'message': '사용자 이름 또는 비밀번호가 잘못되었습니다.'}), 401
        
        # Create session
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        session_id = f'session_{timestamp}_{username}'
        
        # Insert login history
        insert_login_sql = text("""
            INSERT INTO login_history 
            (user_id, session_id, login_time, status, ip_address)
            VALUES 
            (:user_id, :session_id, :login_time, 1, :ip_address)
        """)
        
        db.session.execute(insert_login_sql, {
            'user_id': username,
            'session_id': session_id,
            'login_time': datetime.now(),
            'ip_address': request.remote_addr
        })
        
        db.session.commit()

        session.permanent = True  
        session['user_id'] = username
        session['user_level'] = result.level
        session['name'] = result.name
        session['session_id'] = session_id
        
        response = jsonify({
            'message': '로그인 성공',
            'user_id': username,
            'name': result.name,
            'level': result.level,
            'session_id': session_id
        })
        
        # Set cookie domain and other properties
        session.modified = True
        response.set_cookie(
            'session',
            session_id,  # Use session_id directly
            httponly=True,
            secure=True,
            samesite='None',  # Required for cross-origin requests
            domain='아이피' 
        )
        
        return response, 200
            
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        db.session.rollback()
        return jsonify({
            'message': 'Server error',
            'error': str(e)
        }), 500

@app.route('/api/logout', methods=['POST'])
def logout_api():
    try:
        if 'user_id' in session:
            # Log the logout
            insert_sql = text("""
                INSERT INTO login_history 
                (user_id, session_id, login_time, status, ip_address)
                VALUES 
                (:user_id, :session_id, :login_time, 0, :ip_address)
            """)
            
            db.session.execute(insert_sql, {
                'user_id': session['user_id'],
                'session_id': session.get('session_id', ''),
                'login_time': datetime.now(),
                'ip_address': request.remote_addr
            })
            db.session.commit()

        # Clear session
        session.clear()
        
        response = jsonify({'message': 'Logged out successfully'})
        response.delete_cookie('session')
        return response, 200
        
    except Exception as e:
        logger.error(f"Logout error: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/action', methods=['POST'])
def log_action():
    try:
        data = request.json
        
        # Insert into log table
        insert_sql = text("""
            INSERT INTO log 
            (user_id, action, status, ip_address, timestamp)
            VALUES 
            (:user_id, :action, :status, :ip_address, :timestamp)
        """)
        
        db.session.execute(insert_sql, {
            'user_id': data['user_id'],
            'action': data['action'],
            'status': data['status'],
            'ip_address': request.remote_addr,
            'timestamp': datetime.now()
        })
        
        db.session.commit()
        return jsonify({'status': 'success'}), 201
    except Exception as e:
        logger.error(f"Error logging action: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/ocr', methods=['POST'])
def process_ocr():
    try:
        if 'image' not in request.files:
            logger.error("요청에 이미지 파일이 포함되어 있지 않습니다.")
            return jsonify({'error': "이미지가 없습니다"}), 400

        files = {'image': request.files['image']}
        upload_id = request.form.get('upload_id')

        ocr_url = f"{INTERNAL_SERVICES['ocr']}/ocr_id"
        logger.info(f"Donut OCR 서비스 호출: {ocr_url}")
        
        try:
            ocr_response = requests.post(ocr_url, files=files, timeout=30)
            
            if ocr_response.status_code != 200:
                error_msg = ocr_response.text
                logger.error(f"Donut OCR 실패: {error_msg}")
                return jsonify({
                    'success': False,
                    'error': 'OCR service temporarily unavailable',
                    'technical_details': error_msg
                }), 500

            try:
                ocr_result = ocr_response.json()
            except ValueError:
                logger.error(f"Invalid JSON from OCR service: {ocr_response.text}")
                return jsonify({
                    'success': False,
                    'error': 'Invalid response from OCR service'
                }), 500


            metadata_id = None
            if upload_id and 'ocr_result' in ocr_result:
                try:
              
                    predictions = ocr_result['ocr_result']['predictions']
                    if predictions and len(predictions) > 0:
                        metadata = predictions[0]
                        
                    
                        insert_sql = text("""
                            INSERT INTO metadata_info
                            (upload_id, country, birthday, birthplace, expiritydate,
                             fathersname, idno, issuedate, name, passportno, sex, surname)
                            VALUES
                            (:upload_id, :country, :birthday, :birthplace, :expiritydate,
                             :fathersname, :idno, :issuedate, :name, :passportno, :sex, :surname)
                        """)
                        
                        result = db.session.execute(insert_sql, {
                            'upload_id': upload_id,
                            'country': metadata.get('country'),
                            'birthday': metadata.get('birthday'),
                            'birthplace': metadata.get('birthplace'),
                            'expiritydate': metadata.get('expiritydate'),
                            'fathersname': metadata.get('fathersname'),
                            'idno': metadata.get('idno'),
                            'issuedate': metadata.get('issuedate'),
                            'name': metadata.get('name'),
                            'passportno': metadata.get('passportno'),
                            'sex': metadata.get('sex'),
                            'surname': metadata.get('surname')
                        })
                        
                        db.session.commit()
                        metadata_id = result.lastrowid
                        logger.info(f"Metadata stored successfully with ID: {metadata_id}")
                except Exception as e:
                    logger.error(f"메타데이터 저장 오류: {str(e)}")
                    db.session.rollback()

            return jsonify({
                'success': True,
                'ocr_result': ocr_result.get('ocr_result'),
                'metadata_id': metadata_id,
                'upload_id': upload_id
            }), 200

        except requests.exceptions.RequestException as e:
            logger.error(f"OCR 서비스 통신 오류: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'OCR service communication error'
            }), 500

    except Exception as e:
        error_message = f"OCR 처리 중 오류 발생: {str(e)}"
        logger.error(error_message, exc_info=True)
        return jsonify({
            'success': False,
            'error': error_message
        }), 500

@app.route('/api/uv/process', methods=['POST'])
def process_uv():
    try:
        if 'cropped_uv.jpg' not in request.files:
            logger.error("No UV image in request")
            return jsonify({'error': 'No UV image provided'}), 400

        upload_id = request.form.get('upload_id')
        if not upload_id:
            return jsonify({'error': 'upload_id is required'}), 400

        # Forward the UV image to the UV service
        response = requests.post(
            f"{INTERNAL_SERVICES['uv']}/receive_uv",
            files={'cropped_uv.jpg': request.files['cropped_uv.jpg']},
            data={'upload_id': upload_id},
            timeout=5
        )
        
        if response.status_code != 200:
            return jsonify({'error': 'UV processing failed'}), response.status_code

        return jsonify({
            'success': True,
            'message': 'UV processing started successfully',
            'upload_id': upload_id
        }), 200

    except Exception as e:
        logger.error(f"UV processing error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/upload', methods=['POST'])
def create_upload():
    try:
        data = request.json
        
        # Insert into upload_info table
        insert_sql = text("""
            INSERT INTO upload_info 
            (user_id, session_id, set_id, passport_image, face_image, 
             uv_image, metadata_id, new_flag)
            VALUES 
            (:user_id, :session_id, :set_id, :passport_image, :face_image,
             :uv_image, :metadata_id, 1)
        """)
        
        result = db.session.execute(insert_sql, {
            'user_id': data['user_id'],
            'session_id': data['session_id'],
            'set_id': data['set_id'],
            'passport_image': data.get('passport_image'),
            'face_image': data.get('face_image'),
            'uv_image': data.get('uv_image'),
            'metadata_id': data.get('metadata_id')
        })
        
        db.session.commit()
        return jsonify({'upload_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating upload: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/metadata', methods=['POST'])
def create_metadata():
    try:
        data = request.json
        
      
        def clean_date(date_str):
            return date_str if date_str and date_str.lower() != 'none' else None
            
        def clean_string(s):
            return s if s and s.lower() != 'none' else None

        insert_sql = text("""
            INSERT INTO metadata_info
            (upload_id, country, birthday, birthplace, expiritydate,
             fathersname, idno, issuedate, name, passportno, sex, surname)
            VALUES
            (:upload_id, :country, :birthday, :birthplace, :expiritydate,
             :fathersname, :idno, :issuedate, :name, :passportno, :sex, :surname)
        """)
        
        result = db.session.execute(insert_sql, {
            'upload_id': data['upload_id'],
            'country': clean_string(data.get('country')),
            'birthday': clean_date(data.get('birthday')),
            'birthplace': clean_string(data.get('birthplace')),
            'expiritydate': clean_date(data.get('expiritydate')),
            'fathersname': clean_string(data.get('fathersname')),
            'idno': clean_string(data.get('idno')),
            'issuedate': clean_date(data.get('issuedate')),
            'name': clean_string(data.get('name')),
            'passportno': clean_string(data.get('passportno')),
            'sex': clean_string(data.get('sex')),
            'surname': clean_string(data.get('surname'))
        })
        
        db.session.commit()
        return jsonify({'metadata_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating metadata: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/uv/result/<image_id>', methods=['GET'])
def get_uv_result(image_id):
    try:
        # Query UV result
        query = text("""
            SELECT * FROM uv_result 
            WHERE image_id = :image_id 
            ORDER BY timestamp DESC 
            LIMIT 1
        """)
        
        result = db.session.execute(query, {
            'image_id': image_id
        }).fetchone()
        
        if not result:
            return jsonify({
                'status': 'processing',
                'message': 'UV processing in progress'
            }), 200

        return jsonify({
            'status': 'completed',
            'match_status': 'match' if result.match_status == 1 else 'no_match',
            'similarity_score': float(result.similarity_score),
            'result_id': str(result.result_id)  # Add result_id to response
        }), 200

    except Exception as e:
        logger.error(f"Error getting UV result: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/face_image', methods=['POST'])
def create_face_image():
    try:
        data = request.json
        
        insert_sql = text("""
            INSERT INTO face_image (upload_id, image_path)
            VALUES (:upload_id, :image_path)
        """)
        
        result = db.session.execute(insert_sql, {
            'upload_id': data['upload_id'],
            'image_path': data['image_path']
        })
        
        db.session.commit()
        return jsonify({'image_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating face image: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/visible_image', methods=['POST'])
def create_visible_image():
    try:
        data = request.json
        
        insert_sql = text("""
            INSERT INTO visible_image (upload_id, image_path)
            VALUES (:upload_id, :image_path)
        """)
        
        result = db.session.execute(insert_sql, {
            'upload_id': data['upload_id'],
            'image_path': data['image_path']
        })
        
        db.session.commit()
        return jsonify({'image_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating visible image: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/face_result', methods=['POST'])
def create_face_result():
    try:
        data = request.json
        
        insert_sql = text("""
            INSERT INTO face_result 
            (image_id, visible_image_id, similarity_score, match_status)
            VALUES 
            (:image_id, :visible_image_id, :similarity_score, :match_status)
        """)
        
        result = db.session.execute(insert_sql, {
            'image_id': data['image_id'],
            'visible_image_id': data['visible_image_id'],
            'similarity_score': data['similarity_score'],
            'match_status': data['match_status']
        })
        
        db.session.commit()
        return jsonify({'result_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating face result: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/uv_result', methods=['POST'])
def create_uv_result():
    try:
        data = request.json
        
        insert_sql = text("""
            INSERT INTO uv_result 
            (image_id, model_version, similarity_score, match_status, metadata_id)
            VALUES 
            (:image_id, :model_version, :similarity_score, :match_status, :metadata_id)
        """)
        
        result = db.session.execute(insert_sql, {
            'image_id': data['image_id'],
            'model_version': data['model_version'],
            'similarity_score': data['similarity_score'],
            'match_status': data['match_status'],
            'metadata_id': data['metadata_id']
        })
        
        db.session.commit()
        return jsonify({'result_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating UV result: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/log/final_result', methods=['POST'])
def create_final_result():
    try:
        data = request.json
        logger.info(f"Creating final result with data: {data}")

        if 'report_message' in data:

            uv_result_query = text("""
                SELECT ur.result_id
                FROM uv_result ur
                JOIN uv_image ui ON ur.image_id = ui.image_id
                WHERE ui.upload_id = (
                    SELECT upload_id 
                    FROM face_image 
                    WHERE image_id = (
                        SELECT image_id 
                        FROM face_result 
                        WHERE result_id = :face_result_id
                    )
                )
                ORDER BY ur.timestamp DESC
                LIMIT 1
            """)
            
            uv_result = db.session.execute(uv_result_query, {
                'face_result_id': data['face_result_id']
            }).fetchone()
            
            uv_result_id = uv_result.result_id if uv_result else None
            
            insert_sql = text("""
                INSERT INTO final_result 
                (face_result_id, uv_result_id, match_status, report_message)
                VALUES 
                (:face_result_id, :uv_result_id, :match_status, :report_message)
            """)
            
            result = db.session.execute(insert_sql, {
                'face_result_id': data['face_result_id'],
                'uv_result_id': uv_result_id,
                'match_status': data.get('match_status', 1),
                'report_message': data['report_message']
            })
            
            db.session.commit()
            logger.info(f"Final result created with ID: {result.lastrowid}")
            return jsonify({'result_id': result.lastrowid}), 201


        uv_result_query = text("""
            SELECT ur.result_id, ur.similarity_score, ur.match_status
            FROM uv_result ur
            JOIN uv_image ui ON ur.image_id = ui.image_id
            WHERE ui.upload_id = (
                SELECT upload_id 
                FROM face_image 
                WHERE image_id = (
                    SELECT image_id 
                    FROM face_result 
                    WHERE result_id = :face_result_id
                )
            )
            ORDER BY ur.timestamp DESC
            LIMIT 1
        """)


        face_result_query = text("""
            SELECT fr.result_id, fr.similarity_score, fr.match_status
            FROM face_result fr
            WHERE fr.result_id = :face_result_id
        """)
        
        face_result = db.session.execute(face_result_query, {
            'face_result_id': data['face_result_id']
        }).fetchone()

        if not face_result:
            raise Exception('Face result not found')

        face_result_id = str(face_result.result_id)
        face_score = float(face_result.similarity_score)  # Ensure proper float conversion
        face_verified = face_result.match_status == 1

        logger.info(f"Face verification details - Score: {face_score}, Verified: {face_verified}")

        # Get corresponding UV result using upload_id
        uv_result = db.session.execute(uv_result_query, {
            'face_result_id': face_result_id
        }).fetchone()

        # Handle UV result status
        uv_result_id = None
        uv_score = -1.0
        uv_verified = False
        if uv_result:
            uv_result_id = str(uv_result.result_id)
            uv_score = float(uv_result.similarity_score)
            uv_verified = uv_result.match_status == 1

        report_parts = []
        confidence_level = _get_confidence_level(face_score)
        
        if face_verified:
            report_parts.append(
                f'Face verification passed with {confidence_level.lower()} confidence '
                f'(Similarity: {face_score * 100:.1f}%)'
            )
        else:
            report_parts.append(
                f'Face verification failed '
                f'(Similarity: {face_score * 100:.1f}%)'
            )

        if uv_score == -1:
            report_parts.append(
                'UV security features verification unavailable '
                '(No reference data available)'
            )
            uv_available = False
        else:
            uv_available = True
            uv_confidence = _get_confidence_level(uv_score)
            if uv_verified:
                report_parts.append(
                    f'UV security features verified with {uv_confidence.lower()} confidence '
                    f'(Match rate: {uv_score * 100:.1f}%)'
                )
            else:
                report_parts.append(
                    f'UV security features verification failed '
                    f'(Match rate: {uv_score * 100:.1f}%)'
                )

   
        if face_verified and uv_verified:
            overall_status = "FULLY VERIFIED"
        elif face_verified and not uv_available:
            overall_status = "PARTIALLY VERIFIED (Face Only - UV Unavailable)"
        elif face_verified:
            overall_status = "PARTIALLY VERIFIED (Face Only)"
        elif uv_verified:
            overall_status = "PARTIALLY VERIFIED (UV Only)"
        else:
            overall_status = "VERIFICATION FAILED"

        report_parts.append(f'Overall verification: {overall_status}')
        report_message = ' | '.join(report_parts)

 
        if not uv_available:
            match_status = 1 if face_verified else 0
        else:
            match_status = 2 if (face_verified and uv_verified) else 1 if (face_verified or uv_verified) else 0

 
        insert_sql = text("""
            INSERT INTO final_result 
            (face_result_id, uv_result_id, match_status, report_message)
            VALUES 
            (:face_result_id, :uv_result_id, :match_status, :report_message)
        """)
        
        result = db.session.execute(insert_sql, {
            'face_result_id': face_result_id,
            'uv_result_id': uv_result_id,
            'match_status': match_status,
            'report_message': report_message
        })
        
        db.session.commit()
        logger.info(f"Final result created with ID: {result.lastrowid}")
        return jsonify({'result_id': result.lastrowid}), 201

    except Exception as e:
        logger.error(f"Error creating final result: {str(e)}")
        logger.error(f"Request data: {request.json}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

def _get_confidence_level(score):
    if score >= 0.98: return "Very High"
    if score >= 0.95: return "High"
    if score >= 0.90: return "Medium"
    if score >= 0.80: return "Low"
    return "Very Low"

@app.route('/api/log/uv_image', methods=['POST'])
def create_uv_image():
    try:
        data = request.json
        
        insert_sql = text("""
            INSERT INTO uv_image (upload_id, image_path)
            VALUES (:upload_id, :image_path)
        """)
        
        result = db.session.execute(insert_sql, {
            'upload_id': data['upload_id'],
            'image_path': data['image_path']
        })
        
        db.session.commit()
        return jsonify({'image_id': result.lastrowid}), 201
        
    except Exception as e:
        logger.error(f"Error creating UV image: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/face_recognition', methods=['POST'])
def process_face_recognition():
    try:
        if 'selfie' not in request.files or 'id_card' not in request.files:
            logger.error("Missing required files")
            return jsonify({
                'success': False,
                'error': 'Both selfie and ID card images are required'
            }), 400

        files = {
            'selfie': request.files['selfie'],
            'id_card': request.files['id_card']
        }
        
        face_rec_url = f"{INTERNAL_SERVICES['face_recognition']}/face_recognition"
        logger.info(f"Forwarding to face recognition service: {face_rec_url}")
        
        try:
            response = requests.post(
                face_rec_url,
                files=files,
                timeout=30
            )
            
            logger.info(f"Face recognition response: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                return response.json(), 200
                
            logger.error(f"Face recognition error: {response.text}")
            return jsonify({
                'success': False,
                'error': 'Face recognition service error',
                'details': response.text
            }), response.status_code

        except requests.exceptions.RequestException as e:
            logger.error(f"Face recognition service error: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Face recognition service unavailable'
            }), 503

    except Exception as e:
        logger.error(f"Face recognition error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Main health check endpoint"""
    try:
        services_status = {
            'database': False,
            'face_recognition': False
        }
        
        # Check database
        try:
            db.session.execute(text('SELECT 1'))
            services_status['database'] = True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")


        try:
            response = requests.get(
                f"{INTERNAL_SERVICES['face_recognition']}/health",
                timeout=5,
                headers={'Connection': 'close'}
            )
            services_status['face_recognition'] = response.status_code == 200
        except Exception as e:
            logger.error(f"Face recognition health check failed: {e}")

        is_healthy = all(services_status.values())
        
        return jsonify({
            'status': 'healthy' if is_healthy else 'degraded',
            'services': services_status,
            'timestamp': datetime.now().isoformat()
        }), 200 if is_healthy else 207

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


@app.route('/admin/user_statistics', methods=['GET'])
@admin_required
def admin_get_user_statistics():
    """Get user statistics including total, active, and pending users"""
    try:

        stats_query = text("""
            SELECT 
                COUNT(*) as total_users,
                SUM(CASE WHEN is_use = 'Y' THEN 1 ELSE 0 END) as active_users,
                SUM(CASE WHEN is_use = 'N' THEN 1 ELSE 0 END) as pending_users
            FROM staff
        """)
        
        result = db.session.execute(stats_query).first()
        
        # Get active users in last 24 hours from login_history
        active_users_query = text("""
            SELECT COUNT(DISTINCT user_id) as active_last_24h
            FROM login_history
            WHERE login_time >= NOW() - INTERVAL 24 HOUR
            AND status = 1
        """)
        
        active_result = db.session.execute(active_users_query).first()
        
        stats = {
            'total_users': result.total_users,
            'active_users': result.active_users,
            'pending_users': result.pending_users,
            'active_last_24h': active_result.active_last_24h if active_result else 0
        }

  
        level_query = text("""
            SELECT level, COUNT(*) as count
            FROM staff
            WHERE is_use = 'Y'
            GROUP BY level
            ORDER BY level
        """)
        
        level_results = db.session.execute(level_query)
        stats['users_by_level'] = {
            row.level: row.count for row in level_results
        }

        return jsonify({
            'success': True,
            'statistics': stats
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting user statistics: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

@app.route('/admin/pending_users', methods=['GET'])
@admin_required
def admin_get_pending_users():
    """Get list of users awaiting approval"""
    try:
        query = text("""
            SELECT staff_no, user_id, name, hp, level, reg_date
            FROM staff 
            WHERE is_use = 'N'
            ORDER BY reg_date DESC
        """)
        
        results = db.session.execute(query)
        pending_users = []
        
        for row in results:
            pending_users.append({
                'staff_no': row.staff_no,
                'user_id': row.user_id,
                'name': row.name,
                'phone': row.hp,
                'level': row.level,
                'requested_at': row.reg_date.isoformat() if row.reg_date else None
            })

        return jsonify({
            'success': True,
            'users': pending_users
        }), 200
        
    except Exception as e:
        print(f"Error getting pending users: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

@app.route('/admin/approve_user/<int:staff_no>', methods=['POST'])
@admin_required
def admin_approve_user(staff_no):
    """Approve a user registration"""
    try:
        update_query = text("""
            UPDATE staff 
            SET is_use = 'Y',
                approval_status = 'approved',
                processed_at = NOW()
            WHERE staff_no = :staff_no
            AND is_use = 'N'
        """)
        
        result = db.session.execute(update_query, {'staff_no': staff_no})
        
        if result.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'User not found or already approved'
            }), 404
            
        db.session.commit()
        
  
        log_action(staff_no, 'user_approved', 'User registration approved')
        
        return jsonify({
            'success': True,
            'message': 'User approved successfully'
        }), 200
        
    except Exception as e:
        print(f"Error approving user: {str(e)}")
        print(traceback.format_exc())
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': 'Failed to approve user'
        }), 500

@app.route('/admin/reject_user/<int:staff_no>', methods=['POST', 'DELETE'])
@admin_required
def admin_reject_user(staff_no):
    """Reject and delete a pending user"""
    try:
      
        update_query = text("""
            UPDATE staff 
            SET approval_status = 'rejected',
                processed_at = NOW()
            WHERE staff_no = :staff_no
            AND is_use = 'N'
        """)
        
        db.session.execute(update_query, {'staff_no': staff_no})
        

        delete_query = text("""
            DELETE FROM staff 
            WHERE staff_no = :staff_no
            AND is_use = 'N'
        """)
        
        result = db.session.execute(delete_query, {'staff_no': staff_no})
        
        if result.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'User not found or already approved'
            }), 404
            
        db.session.commit()
        
        # Log the rejection action
        log_action(staff_no, 'user_rejected', 'User registration rejected')
        
        return jsonify({
            'success': True,
            'message': 'User rejected successfully'
        }), 200
        
    except Exception as e:
        print(f"Error rejecting user: {str(e)}")
        print(traceback.format_exc())
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': 'Failed to reject user'
        }), 500

@app.route('/admin/users', methods=['GET'])
@admin_required
def admin_get_all_users():
    """Get list of all users"""
    try:
        query = text("""
            SELECT s.staff_no, s.user_id, s.name, s.level, s.hp, s.is_use, 
                   s.reg_date, s.approval_status, s.processed_at,
                   MAX(l.login_time) as last_login
            FROM staff s
            LEFT JOIN login_history l ON s.user_id = l.user_id
            GROUP BY s.staff_no, s.user_id, s.name, s.level, s.hp, s.is_use, 
                     s.reg_date, s.approval_status, s.processed_at
            ORDER BY s.reg_date DESC
        """)
        
        results = db.session.execute(query)
        users = []
        
        for row in results:
            users.append({
                'staff_no': row.staff_no,
                'user_id': row.user_id,
                'name': row.name,
                'level': row.level,
                'phone': row.hp,
                'is_active': row.is_use == 'Y',
                'registered_at': row.reg_date.isoformat() if row.reg_date else None,
                'approval_status': row.approval_status,
                'processed_at': row.processed_at.isoformat() if row.processed_at else None,
                'last_login': row.last_login.isoformat() if row.last_login else None
            })

        return jsonify({
            'success': True,
            'users': users
        }), 200
        
    except Exception as e:
        print(f"Error getting users: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

@app.route('/admin/search_users', methods=['GET'])
@admin_required
def admin_search_users():
    """Search users by name or user ID"""
    try:
        search_term = request.args.get('q', '').strip()
        if not search_term:
            return jsonify({
                'success': False,
                'message': 'Search term is required'
            }), 400

        query = text("""
            SELECT s.staff_no, s.user_id, s.name, s.level, s.hp, s.is_use, 
                   s.reg_date, s.approval_status, s.processed_at,
                   MAX(l.login_time) as last_login
            FROM staff s
            LEFT JOIN login_history l ON s.user_id = l.user_id
            WHERE LOWER(s.name) LIKE :search_term 
            OR LOWER(s.user_id) LIKE :search_term
            GROUP BY s.staff_no, s.user_id, s.name, s.level, s.hp, s.is_use, 
                     s.reg_date, s.approval_status, s.processed_at
            ORDER BY s.reg_date DESC
        """)
        
        results = db.session.execute(query, {
            'search_term': f'%{search_term.lower()}%'
        })
        
        users = []
        for row in results:
            users.append({
                'staff_no': row.staff_no,
                'user_id': row.user_id,
                'name': row.name,
                'level': row.level,
                'phone': row.hp,
                'is_active': row.is_use == 'Y',
                'registered_at': row.reg_date.isoformat() if row.reg_date else None,
                'approval_status': row.approval_status,
                'processed_at': row.processed_at.isoformat() if row.processed_at else None,
                'last_login': row.last_login.isoformat() if row.last_login else None
            })

        return jsonify({
            'success': True,
            'users': users
        }), 200
        
    except Exception as e:
        print(f"Error searching users: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

@app.route('/admin/user/<int:staff_no>', methods=['GET'])
@admin_required
def admin_get_user_details(staff_no):
    """Get detailed information for a specific user"""
    try:
        query = text("""
            SELECT s.staff_no, s.user_id, s.name, s.level, s.hp, s.is_use, 
                   s.reg_date, s.approval_status, s.processed_at,
                   MAX(l.login_time) as last_login,
                   COUNT(DISTINCT l.login_id) as login_count
            FROM staff s
            LEFT JOIN login_history l ON s.user_id = l.user_id
            WHERE s.staff_no = :staff_no
            GROUP BY s.staff_no, s.user_id, s.name, s.level, s.hp, s.is_use, 
                     s.reg_date, s.approval_status, s.processed_at
        """)
        
        result = db.session.execute(query, {'staff_no': staff_no}).first()
        
        if not result:
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404
            
        user_details = {
            'staff_no': result.staff_no,
            'user_id': result.user_id,
            'name': result.name,
            'level': result.level,
            'phone': result.hp,
            'is_active': result.is_use == 'Y',
            'registered_at': result.reg_date.isoformat() if result.reg_date else None,
            'approval_status': result.approval_status,
            'processed_at': result.processed_at.isoformat() if result.processed_at else None,
            'last_login': result.last_login.isoformat() if result.last_login else None,
            'login_count': result.login_count
        }

        return jsonify({
            'success': True,
            'user': user_details
        }), 200
        
    except Exception as e:
        print(f"Error getting user details: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

@app.route('/admin/update_user/<int:staff_no>', methods=['PUT'])
@admin_required
def admin_update_user(staff_no):
    """Update user details"""
    try:
        data = request.get_json()
        updates = []
        params = {'staff_no': staff_no}
        

        if 'level' in data:
            updates.append("level = :level")
            params['level'] = data['level']
            
        if 'is_active' in data:
            updates.append("is_use = :is_use")
            params['is_use'] = 'Y' if data['is_active'] else 'N'
            
        if not updates:
            return jsonify({
                'success': False,
                'message': 'No updates provided'
            }), 400
            
        update_query = text(f"""
            UPDATE staff 
            SET {', '.join(updates)}
            WHERE staff_no = :staff_no
        """)
        
        result = db.session.execute(update_query, params)
        
        if result.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404
            
        db.session.commit()
  
        log_action(staff_no, 'user_updated', f"User details updated: {', '.join(updates)}")
        
        return jsonify({
            'success': True,
            'message': 'User updated successfully'
        }), 200
        
    except Exception as e:
        print(f"Error updating user: {str(e)}")
        print(traceback.format_exc())
        db.session.rollback()
        return jsonify({
            'success': False,
                'message': 'Failed to update user'
        }), 500

@app.route('/admin/reset_password/<int:staff_no>', methods=['POST'])
@admin_required
def admin_reset_user_password(staff_no):
    """Reset a user's password to a random temporary password"""
    try:
      
        temp_password = secrets.token_urlsafe(12)
        
     
        password_hash = hashlib.sha256(temp_password.encode()).hexdigest()
        
        update_query = text("""
            UPDATE staff 
            SET passwd = :password
            WHERE staff_no = :staff_no
        """)
        
        result = db.session.execute(update_query, {
            'staff_no': staff_no,
            'password': password_hash
        })
        
        if result.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404
            
        db.session.commit()
        
     
        log_action(staff_no, 'password_reset', 'Password reset by admin')
        
        return jsonify({
            'success': True,
            'message': 'Password reset successfully',
            'temporary_password': temp_password
        }), 200
        
    except Exception as e:
        print(f"Error resetting password: {str(e)}")
        print(traceback.format_exc())
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': 'Failed to reset password'
        }), 500

@app.route('/admin/user_activity/<int:staff_no>', methods=['GET'])
@admin_required
def admin_get_user_activity(staff_no):
    """Get activity history for a specific user"""
    try:
        days = request.args.get('days', 30, type=int)
        if days <= 0:
            days = 30
            
        query = text("""
            SELECT l.action, l.status, l.ip_address, l.timestamp
            FROM log l
            JOIN staff s ON l.user_id = s.user_id
            WHERE s.staff_no = :staff_no
            AND l.timestamp >= NOW() - INTERVAL :days DAY
            ORDER BY l.timestamp DESC
        """)
        
        results = db.session.execute(query, {
            'staff_no': staff_no,
            'days': days
        })
        
        activities = []
        for row in results:
            activities.append({
                'action': row.action,
                'status': row.status,
                'ip_address': row.ip_address,
                'timestamp': row.timestamp.isoformat()
            })

        return jsonify({
            'success': True,
            'activities': activities
        }), 200
        
    except Exception as e:
        print(f"Error getting user activity: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500

def log_action(staff_no, action_type, description):
    """Helper function to log user-related actions"""
    try:
        user_query = text("SELECT user_id FROM staff WHERE staff_no = :staff_no")
        user_result = db.session.execute(user_query, {'staff_no': staff_no}).first()
        
        if not user_result:
            return
            
        insert_query = text("""
            INSERT INTO log (user_id, action, status, ip_address, timestamp)
            VALUES (:user_id, :action, 'success', :ip_address, NOW())
        """)
        
        db.session.execute(insert_query, {
            'user_id': user_result.user_id,
            'action': f"{action_type}: {description}",
            'ip_address': request.remote_addr
        })
        
        db.session.commit()
    except Exception as e:
        logger.error(f"Error logging action: {str(e)}")
        logger.error(traceback.format_exc())
        db.session.rollback()


app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None', 
    SESSION_COOKIE_DOMAIN='아이피', 
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)  
)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=4885)
