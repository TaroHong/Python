import requests
from bs4 import BeautifulSoup
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import schedule
import time
from datetime import datetime, timedelta
import re

# 크롤링할 URL
target_url = "https://corearoadbike.com/board/board.php?t_id=Menu01Top6&category=%25ED%258C%2590%25EB%25A7%25A4&sort=wr_last+desc"

# 텔레그램 봇 정보
TELEGRAM_BOT_TOKEN = '7272596527:AAE9de-Uw58CheN-ayHQoL1_MSPxur2O0b4'
TELEGRAM_CHAT_ID = '7321984689'

# 확인된 게시물 저장용 파일 경로
log_file_path = 'checked_posts.log'

# 검색할 키워드 리스트
keywords = ["에이토스", "마돈", "S5","벤지","라파","시스템식스","에스웍스","에스웍"]

# 정규 표현식으로 키워드 패턴 생성
keyword_pattern = re.compile('|'.join(keywords))

# 텔레그램 메시지 전송 함수
def send_telegram_message(message):
    send_text = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage?chat_id={TELEGRAM_CHAT_ID}&text={message}'
    response = requests.get(send_text)
    print(f"텔레그램 메시지 전송 상태: {response.status_code}")
    return response.json()

# 로그 파일에서 확인된 게시물 읽기
def load_checked_posts():
    try:
        with open(log_file_path, 'r', encoding='utf-8') as file:
            checked_posts = set(file.read().splitlines())
        return checked_posts
    except FileNotFoundError:
        return set()

# 새로운 확인된 게시물 로그 파일에 추가
def save_checked_post(post_title):
    with open(log_file_path, 'a', encoding='utf-8') as file:
        file.write(post_title + '\n')

# 사이트 크롤링 함수
def crawl_site():
    try:
        print("크롤링 시작...")
        checked_posts = load_checked_posts()
        
        # User-Agent 헤더 추가
        req = Request(target_url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urlopen(req)
        print("사이트 응답 수신 완료")
        soup = BeautifulSoup(response, "html.parser")
        titles = soup.find_all('td', attrs={'class': 'list_title_B'})
        
        if not titles:
            print("게시글을 찾을 수 없습니다.")
        
        for title in titles:
            post_title = title.text.strip()
            if post_title in checked_posts:
                continue  # 이미 확인된 게시물은 무시
            
            post_link_element = title.find_parent('tr').find('a')
            if post_link_element:
                post_link = f"https://corearoadbike.com{post_link_element['href']}"
                print(f"제목: {post_title}")
                print(f"링크: {post_link}")
                if keyword_pattern.search(post_title):
                    print(f"키워드 발견: {post_title}")
                    message = f"게시물 발견: {post_title}\n{post_link}"
                    send_telegram_message(message)
                    save_checked_post(post_title)  # 확인된 게시물을 로그 파일에 저장
                    checked_posts.add(post_title)  # 확인된 게시물 추가
            else:
                print(f"게시물 링크를 찾을 수 없습니다: {post_title}")
    
    except HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.reason}")
    except Exception as e:
        print(f"예외 발생: {e}")


# 남은 시간 계산 및 표시 함수
def display_remaining_time(next_run_time):
    while True:
        now = datetime.now()
        remaining_time = next_run_time - now
        if remaining_time.total_seconds() <= 0:
            break
        print(f"다음 실행까지 남은 시간: {remaining_time}")
        time.sleep(1)

# 메인 함수
if __name__ == "__main__":
    # 초기에 한 번 실행하여 테스트
    crawl_site()

    # 1분마다 crawl_site 함수를 실행하도록 스케줄 설정
    schedule.every(1).minutes.do(crawl_site)
    
    print("스케줄러 시작...")
    while True:
        next_run_time = datetime.now() + timedelta(minutes=1)
        display_remaining_time(next_run_time)
        schedule.run_pending()
        time.sleep(1)
