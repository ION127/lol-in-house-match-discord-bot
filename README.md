# 🎮 롤 내전 봇

League of Legends 내전(커스텀 게임) 모집 및 팀 구성을 자동화하는 Discord 봇입니다.  
웹 관리자 페이지를 통해 내전, 유저, 차단, 로그를 한눈에 관리할 수 있습니다.

---

## ✨ 주요 기능

### Discord 봇
| 기능 | 설명 |
|------|------|
| **내전 모집** | `/내전시작` 으로 모집 공고 임베드 생성, 버튼으로 참가 신청 |
| **팀 선택** | 참가 시 1팀 / 2팀 직접 지정, 포지션(탑·정글·미드·원딜·서폿·무관) 선택 |
| **대기열** | 인원 초과 시 자동 대기, 빠진 자리 생기면 자동 승격 |
| **팀 밸런싱** | `/팀섞기` — 티어 기반 자동 밸런싱 (스네이크 드래프트) |
| **라이엇 ID 등록** | `/프로필등록` 으로 Riot ID·솔로랭크 자동 조회 및 저장 |
| **자동 만료** | 개설 후 12시간이 지난 내전 자동 종료, 개설자에게만 DM |
| **포지션 중복 허용** | 같은 포지션 여러 명 가능, 선착순 우선 배정 |

### 웹 관리자 페이지
| 메뉴 | 기능 |
|------|------|
| **대시보드** | 활성 내전·유저 수·차단 현황 요약 |
| **내전 관리** | 상태별 필터, 상세 보기, 참가자 강제 퇴장, 내전 강제 취소 |
| **유저 관리** | 라이엇 ID 검색, 차단, 데이터 삭제 |
| **차단 관리** | 기간·영구 차단, 해제 |
| **설정** | 서버별 알림 역할, 로그 채널, 쿨타임, 최대 인원 설정 |
| **로그** | 전체 봇 활동 로그, 이벤트 종류·서버 필터, 자동 새로고침, CSV 내보내기 |

---

## 🛠️ 기술 스택

- **Python 3.11+**
- **discord.py 2.x** — 슬래시 커맨드, 버튼, 모달, 셀렉트 메뉴
- **SQLite + aiosqlite** — 비동기 데이터베이스
- **FastAPI + uvicorn** — 웹 관리자 API 서버
- **Riot Games API** — 소환사 정보·티어 조회
- **Tailwind CSS (CDN)** — 웹 관리자 UI

---

## 🚀 설치 및 실행

### 1. 저장소 클론

```bash
git clone https://github.com/ION127/lol-in-house-match-discord-bot.git
cd lol-in-house-match-discord-bot
```

### 2. Python 패키지 설치

```bash
pip install -r requirements.txt
```

### 3. 환경 변수 설정

`.env.example` 을 복사해 `.env` 파일을 만들고 실제 값을 입력합니다.

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

```env
DISCORD_TOKEN=여기에_디스코드_봇_토큰
RIOT_API_KEY=여기에_라이엇_API_키
DATABASE_PATH=bot.db
WEB_PORT=8080
WEB_SECRET=관리자페이지_비밀번호
```

> **⚠️ `.env` 파일은 절대 GitHub에 올리지 마세요.** `.gitignore`에 의해 자동으로 제외됩니다.

### 4. Discord 봇 설정

[Discord Developer Portal](https://discord.com/developers/applications)에서:

1. 새 Application 생성 → **Bot** 탭 → 토큰 복사 → `.env`의 `DISCORD_TOKEN`에 입력
2. **Privileged Gateway Intents** 에서 **Server Members Intent**, **Message Content Intent** 활성화
3. **OAuth2 → URL Generator**: `bot` + `applications.commands` 스코프, 필요한 권한 체크 후 초대 링크 생성

### 5. Riot API 키 발급

[Riot Developer Portal](https://developer.riotgames.com)에서 키 발급 후 `.env`의 `RIOT_API_KEY`에 입력합니다.  
Development Key는 24시간마다 갱신 필요. 지속 운영 시 Production Key 신청 권장.

### 6. 실행

```bash
python main.py
```

봇 실행과 동시에 웹 관리자 페이지가 `http://localhost:8080` 에서 시작됩니다.

---

## 📁 프로젝트 구조

```
.
├── main.py              # 봇 + 웹 서버 진입점
├── config.py            # 환경 변수 로드
├── database.py          # SQLite 스키마 및 쿼리
├── riot_api.py          # Riot API 호출
├── cogs/
│   ├── lobby.py         # 내전 모집 관련 커맨드 및 뷰
│   ├── team.py          # 팀 밸런싱 (/팀섞기)
│   ├── profile.py       # 라이엇 ID 등록 (/프로필등록)
│   └── admin.py         # (웹으로 이전됨)
├── web/
│   ├── app.py           # FastAPI 라우터
│   └── static/
│       └── index.html   # 웹 관리자 SPA
├── .env.example         # 환경 변수 예시
├── requirements.txt
└── README.md
```

---

## ⚙️ 주요 슬래시 커맨드

| 커맨드 | 설명 |
|--------|------|
| `/내전시작` | 내전 모집 공고 생성 |
| `/내전취소` | 내가 개설한 내전 취소 |
| `/팀섞기` | 참가자 티어 기반 자동 팀 배정 |
| `/프로필등록` | 라이엇 ID 등록 및 티어 조회 |
| `/프로필보기` | 내 프로필 확인 |

---

## 🔐 관리자 설정

`config.py` 의 `ADMIN_USER_IDS` 에 관리 권한을 가질 Discord 유저 ID(숫자)를 등록하세요.

```python
ADMIN_USER_IDS: set[int] = {
    123456789012345678,  # 관리자1
    234567890123456789,  # 관리자2
}
```

웹 관리자 페이지 로그인 비밀번호는 `.env`의 `WEB_SECRET` 값입니다.

---

## 📝 라이선스

MIT License
