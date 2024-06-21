import requests
from bs4 import BeautifulSoup
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import schedule
import time
from datetime import datetime, timedelta
import re
from urllib.parse import urlparse, parse_qs, urlencode

# 크롤링할 URL
target_url = "https://corearoadbike.com/board/board.php?t_id=Menu01Top6&category=%25ED%258C%2590%25EB%25A7%25A4&sort=wr_last+desc"

# ... (나머지 코드는 동일)

def create_full_url(base_url, href):
    parsed_base = urlparse(base_url)
    parsed_href = urlparse(href)
    
    # 기존 URL의 쿼리 파라미터 가져오기
    base_query = parse_qs(parsed_base.query)
    
    # 새 URL의 쿼리 파라미터 가져오기
    new_query = parse_qs(parsed_href.query)
    
    # 두 쿼리 파라미터 합치기 (새 URL의 파라미터가 우선)
    combined_query = {**base_query, **new_query}
    
    # URL 재구성
    final_url = f"{parsed_base.scheme}://{parsed_base.netloc}{parsed_href.path}?{urlencode(combined_query, doseq=True)}"
    
    return final_url

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
            
            # 게시글 링크 추출
            link = title.find('a')
            if link and 'href' in link.attrs:
                post_url = create_full_url(target_url, link['href'])
            else:
                post_url = "링크를 찾을 수 없습니다."
            
            print(f"제목: {post_title}")
            if keyword_pattern.search(post_title):
                print(f"키워드 발견: {post_title}")
                message = f"게시물 발견: {post_title}\n링크: {post_url}"
                send_telegram_message(message)
                save_checked_post(post_title)  # 확인된 게시물을 로그 파일에 저장
                checked_posts.add(post_title)  # 확인된 게시물 추가
    
    except HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.reason}")
    except Exception as e:
        print(f"예외 발생: {e}")

# ... (나머지 코드는 동일)
