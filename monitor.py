import base64
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

import requests
from lxml import etree
from playwright.sync_api import sync_playwright

# =========================
# 可调参数
# =========================
PORTAL_URL = "https://yjsjw-443.webvpn.scut.edu.cn/"
# "https://yjsjw.scut.edu.cn/"

TARGET_URL = (
    f"{PORTAL_URL}psc/ps_9/EMPLOYEE/SA/c/"
    "SC_CUSTOM_MENU.SC_BS_PP_REC_COM.GBL"
    "?FolderPath=PORTAL_ROOT_OBJECT.SC_XWGL_MGT.SC_BSXWGL_MGT."
    "SC_BSLWSSGL_MGT.SC_BS_PP_REC_COM_GBL"
    "&IsFolder=false"
    "&IgnoreParamTempl=FolderPath%2cIsFolder"
)

HOMEPAGE_REFERER = (
    f"{PORTAL_URL}psp/ps/EMPLOYEE/SA/s/"
    "WEBLIB_PTPP_SC.HOMEPAGE.FieldFormula.IScript_AppHP"
    "?pt_fname=CO_EMPLOYEE_SELF_SERVICE"
    "&FolderPath=PORTAL_ROOT_OBJECT.CO_EMPLOYEE_SELF_SERVICE"
    "&IsFolder=true"
)

SUCCESS_TEXT = "研究生教学教务管理系统"
WATCH_XPATH = '//*[@id="SC_DGRD_PP_APY_SC_ZP_DESCR$0"]'
COOKIE_FILE = "cookies.json"
MONITOR_INTERVAL_SECONDS = 5 * 60
REQUEST_TIMEOUT_SECONDS = 30
LOGIN_POLL_SECONDS = 2
QRCODE_MAX_AGE_SECONDS = 55
MAX_QRCODE_NOTIFY_COUNT = 5
QRCODE_EXPIRED_KEYWORDS = (
    "二维码已失效",
    "二维码失效",
    "二维码过期",
    "请刷新二维码",
    "expired",
    "invalid",
)

NOTIFY_URL = "http://14.103.144.178:7790/send/friend"
NOTIFY_TARGET = "1061700625"
NOTIFY_KEY = "lihua"

# 登录二维码与图床
QRCODE_SELECTOR = "#qrcodeQQLogin img"
QRCODE_IMAGE_FILE = "login_qrcode.png"
QRCODE_URL_FILE = "login_qrcode_url.txt"
IMAGE_UPLOAD_API_URL = "https://img.scdn.io/api/v1.php"
IMAGE_UPLOAD_OUTPUT_FORMAT = "png"
IMAGE_UPLOAD_CDN_DOMAIN = "default"
IMAGE_UPLOAD_TIMEOUT_SECONDS = 30

# Server酱 Turbo
# 发送地址格式为 https://sctapi.ftqq.com/<SendKey>.send
# 不使用时留空即可
SERVERCHAN_SENDKEY = ""
SERVERCHAN_API_TEMPLATE = "https://sctapi.ftqq.com/{sendkey}.send"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Referer": HOMEPAGE_REFERER,
    "Connection": "close",
}


@dataclass
class MonitorState:
    last_text: Optional[str] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    session_invalid_notified: bool = False


# =========================
# 通知
# =========================
def send_message_via_notify_url(msg: str) -> None:
    resp = requests.get(
        NOTIFY_URL,
        params={
            "target": NOTIFY_TARGET,
            "msg": msg,
            "key": NOTIFY_KEY,
        },
        timeout=10,
    )
    resp.raise_for_status()


def send_message_via_serverchan(title: str, desp: str) -> None:
    sendkey = SERVERCHAN_SENDKEY.strip()
    if not sendkey:
        print("未配置 Server酱 SendKey，跳过 Server酱通知。")
        return

    api_url = SERVERCHAN_API_TEMPLATE.format(sendkey=sendkey)
    resp = requests.post(
        api_url,
        data={
            "title": title,
            "desp": desp,
        },
        timeout=10,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        data = None

    if isinstance(data, dict) and data.get("code") not in (0, None):
        raise RuntimeError(f"Server酱返回异常: {data}")


def send_message(msg: str, title: str = "教务监控通知") -> None:
    errors = []

    try:
        send_message_via_notify_url(msg)
        print("NOTIFY_URL 通知发送成功")
    except Exception as exc:
        errors.append(f"NOTIFY_URL: {exc}")
        print("NOTIFY_URL 通知发送失败", exc)

    try:
        send_message_via_serverchan(title=title, desp=msg)
        if SERVERCHAN_SENDKEY.strip():
            print("Server酱通知发送成功")
    except Exception as exc:
        errors.append(f"Server酱: {exc}")
        print("Server酱通知发送失败", exc)

    if errors:
        print("部分通知通道失败 -> " + " | ".join(errors))


def send_notification(old_text: str, new_text: str) -> None:
    print("=" * 80)
    print("检测到内容更新")
    print(f"更新前: {old_text!r}")
    print(f"更新后: {new_text!r}")
    print("=" * 80)

    title = "教务状态已更新"
    msg = f"教务状态已更新\n更新前: {old_text}\n更新后: {new_text}"
    send_message(msg, title=title)


def send_session_invalid_notification(
    msg: str = "教务系统登录已失效，请尽快重新登录处理。"
) -> None:
    send_message(msg, title="教务系统登录态失效")


# =========================
# 登录二维码与图床
# =========================
def extract_login_qrcode(page, image_file: str = QRCODE_IMAGE_FILE) -> str:
    img = page.locator(QRCODE_SELECTOR).first
    img.wait_for(state="attached", timeout=30000)
    src = img.get_attribute("src")

    if not src or not src.startswith("data:image/"):
        raise RuntimeError("未获取到 base64 登录二维码。")

    try:
        _, encoded = src.split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception as exc:
        raise RuntimeError("登录二维码 base64 解码失败。") from exc

    with open(image_file, "wb") as f:
        f.write(image_bytes)

    print(f"已提取登录二维码图片: {image_file}")
    return image_file


def upload_image_to_img_host(
    image_file: str,
    url_file: str = QRCODE_URL_FILE,
) -> str:
    data = {"outputFormat": IMAGE_UPLOAD_OUTPUT_FORMAT}
    if IMAGE_UPLOAD_CDN_DOMAIN:
        data["cdn_domain"] = IMAGE_UPLOAD_CDN_DOMAIN

    with open(image_file, "rb") as f:
        resp = requests.post(
            IMAGE_UPLOAD_API_URL,
            files={"image": (os.path.basename(image_file), f)},
            data=data,
            timeout=IMAGE_UPLOAD_TIMEOUT_SECONDS,
        )

    resp.raise_for_status()

    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"图床返回非 JSON 响应: {resp.text[:200]}") from exc

    if not isinstance(payload, dict) or not payload.get("success"):
        raise RuntimeError(f"图床上传失败: {payload}")

    url = payload.get("url") or payload.get("data", {}).get("url")
    if not url:
        raise RuntimeError(f"图床返回中缺少 url: {payload}")

    with open(url_file, "w", encoding="utf-8") as f:
        f.write(url)

    print(f"登录二维码图床地址: {url}")
    return url

def get_qrcode_src(page) -> Optional[str]:
    try:
        img = page.locator(QRCODE_SELECTOR).first
        if img.count() == 0:
            return None
        return img.get_attribute("src")
    except Exception:
        return None


def is_qrcode_expired(page) -> bool:
    page_text = ""
    try:
        page_text = page.inner_text("body", timeout=2000) or ""
    except Exception:
        pass

    lowered = page_text.lower()
    return any(keyword.lower() in lowered for keyword in QRCODE_EXPIRED_KEYWORDS)


def send_login_qrcode_notification(
    page,
    last_qrcode_src: Optional[str] = None,
    qrcode_notify_count: int = 0,
    qrcode_notify_muted: bool = False,
) -> tuple[Optional[str], int, bool]:
    current_src = get_qrcode_src(page)
    if not current_src:
        return last_qrcode_src, qrcode_notify_count, qrcode_notify_muted
    if current_src == last_qrcode_src:
        return last_qrcode_src, qrcode_notify_count, qrcode_notify_muted
    if qrcode_notify_muted:
        print("检测到新二维码，但已达到通知上限，跳过发送通知。")
        return current_src, qrcode_notify_count, qrcode_notify_muted
    qrcode_file = extract_login_qrcode(page)
    qrcode_url = upload_image_to_img_host(qrcode_file)
    send_message(
        f"教务系统登录二维码：\n{qrcode_url}",
        title="教务系统登录二维码",
    )
    qrcode_notify_count += 1
    print(f"已发送第 {qrcode_notify_count} 次登录二维码通知。")
    if qrcode_notify_count >= MAX_QRCODE_NOTIFY_COUNT:
        qrcode_notify_muted = True
        print("已达到二维码通知上限，后续二维码刷新将不再发送通知。")
    return current_src, qrcode_notify_count, qrcode_notify_muted

def refresh_login_page_and_qrcode(
    page,
    reason: str,
    qrcode_notify_count: int,
    qrcode_notify_muted: bool,
) -> tuple[Optional[str], float, int, bool]:
    print(f"检测到登录二维码需要刷新，原因: {reason}")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    page.locator(QRCODE_SELECTOR).first.wait_for(state="attached", timeout=30000)
    last_qrcode_src, qrcode_notify_count, qrcode_notify_muted = (
        send_login_qrcode_notification(
            page,
            last_qrcode_src=None,
            qrcode_notify_count=qrcode_notify_count,
            qrcode_notify_muted=qrcode_notify_muted,
        )
    )
    return last_qrcode_src, time.time(), qrcode_notify_count, qrcode_notify_muted

# =========================
# 浏览器安装检测
# =========================
def ensure_browser_installed() -> None:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        error_text = str(exc)
        if "Executable doesn't exist" in error_text:
            print("未检测到 Chromium，开始自动安装...")
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
            )
            print("Chromium 安装完成。")
        else:
            raise


# =========================
# 浏览器
# =========================
def build_browser():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=False,
        args=["--window-size=960,720"],
    )
    context = browser.new_context(viewport={"width": 960, "height": 720})
    page = context.new_page()
    return playwright, browser, context, page


# =========================
# 登录等待
# =========================
def wait_for_manual_login(page) -> None:
    page.goto(PORTAL_URL, wait_until="domcontentloaded")
    print("请在浏览器中手动登录，出现“研究生教学教务管理系统”后会继续。")

    last_qrcode_src = None
    qrcode_sent_at = 0.0
    qrcode_notify_count = 0
    qrcode_notify_muted = False

    page.locator(QRCODE_SELECTOR).first.wait_for(state="attached", timeout=30000)
    last_qrcode_src, qrcode_notify_count, qrcode_notify_muted = (
        send_login_qrcode_notification(
            page,
            last_qrcode_src,
            qrcode_notify_count,
            qrcode_notify_muted,
        )
    )
    qrcode_sent_at = time.time()

    while True:
        if page.locator(f"text={SUCCESS_TEXT}").count() > 0:
            print("检测到登录成功。")
            return

        current_qrcode_src = get_qrcode_src(page)

        if current_qrcode_src and current_qrcode_src != last_qrcode_src:
            last_qrcode_src, qrcode_notify_count, qrcode_notify_muted = (
                send_login_qrcode_notification(
                    page,
                    last_qrcode_src,
                    qrcode_notify_count,
                    qrcode_notify_muted,
                )
            )
            qrcode_sent_at = time.time()

        expired = is_qrcode_expired(page)
        aged = (time.time() - qrcode_sent_at) >= QRCODE_MAX_AGE_SECONDS

        if expired or aged:
            reason = (
                "页面提示二维码失效"
                if expired
                else "二维码超时未刷新"
            )
            (
                last_qrcode_src,
                qrcode_sent_at,
                qrcode_notify_count,
                qrcode_notify_muted,
            ) = refresh_login_page_and_qrcode(
                page,
                reason,
                qrcode_notify_count,
                qrcode_notify_muted,
            )
            continue

        page.wait_for_timeout(LOGIN_POLL_SECONDS * 1000)

# =========================
# cookie 相关
# =========================
def sync_cookies(context, session: requests.Session) -> None:
    session.cookies.clear()
    cookies = context.cookies()
    for cookie in cookies:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )

def save_cookies(context, cookie_file: str = COOKIE_FILE) -> None:
    cookies = context.cookies()
    with open(cookie_file, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print("已保存 cookies。")


def load_cookies(session: requests.Session, cookie_file: str = COOKIE_FILE) -> None:
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    for cookie in cookies:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )


def rebuild_session(cookie_file: str = COOKIE_FILE) -> requests.Session:
    session = requests.Session()
    load_cookies(session, cookie_file=cookie_file)
    return session


# =========================
# requests 访问与解析
# =========================
def fetch_page(session: requests.Session) -> tuple[Optional[str], requests.Response]:
    resp = session.get(
        TARGET_URL,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()

    html = etree.HTML(resp.text)
    if html is None:
        return None, resp

    result = html.xpath(WATCH_XPATH)
    if not result:
        return None, resp

    text = "".join(result[0].itertext()).strip()
    return text, resp


def is_session_invalid(resp: requests.Response, watched_text: Optional[str]) -> bool:
    body = resp.text or ""
    url = resp.url or ""

    if "You are not authorized to access this component" in body:
        return True
    if "PSLOGIN" in url.upper():
        return True
    if "signin" in url.lower() or "login" in url.lower():
        return True
    if watched_text is None and ("登录" in body or "login" in body.lower()):
        return True

    return False


# =========================
# 可中断等待
# =========================
def interruptible_wait(
    stop_event: threading.Event,
    total_seconds: int,
    step: float = 1.0,
) -> bool:
    """
    可被 Ctrl+C 更及时打断的等待。
    返回 True 表示 stop_event 已被置位，返回 False 表示自然等完。
    """
    end_time = time.time() + total_seconds
    while time.time() < end_time:
        remaining = end_time - time.time()
        timeout = min(step, max(0.0, remaining))
        if stop_event.wait(timeout):
            return True
    return False


# =========================
# session 失效处理
# =========================
def handle_session_invalid(state: MonitorState, session: requests.Session) -> bool:
    if not state.session_invalid_notified:
        send_session_invalid_notification("教务系统登录态已失效，请你重新处理登录。")
        state.session_invalid_notified = True

    print("准备临时拉起浏览器，等待你手动重新登录。")

    playwright = None
    browser = None
    try:
        ensure_browser_installed()
        playwright, browser, context, page = build_browser()
        wait_for_manual_login(page)
        sync_cookies(context, session)
        save_cookies(context)

        send_message(
            "教务系统重新登录成功，监控已恢复。",
            title="教务系统重新登录成功",
        )

        state.session_invalid_notified = False
        print("已重新获取登录态，浏览器已关闭，继续监控。")
        return True

    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"重新登录失败: {exc}")
        traceback.print_exc()
        return False
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

# =========================
# 监控主循环
# =========================
def monitor_loop(session: requests.Session, state: MonitorState) -> None:
    while not state.stop_event.is_set():
        try:
            watched_text, resp = fetch_page(session)

            if is_session_invalid(resp, watched_text):
                ok = handle_session_invalid(state, session)
                if not ok:
                    break
                continue

            state.session_invalid_notified = False

            if watched_text is None:
                raise RuntimeError("XPath 未匹配到目标内容，页面结构可能变化。")

            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"[{now_str}] 当前内容: {watched_text!r}")

            if state.last_text is None:
                state.last_text = watched_text
                print("已记录初始内容。")
            elif watched_text != state.last_text:
                old_text = state.last_text
                state.last_text = watched_text
                send_notification(old_text, watched_text)
            else:
                print("内容未变化。")

        except KeyboardInterrupt:
            raise

        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            print(f"[监控] HTTP异常: {exc}")
            traceback.print_exc()

            if status_code is not None and 500 <= status_code < 600:
                print(f"检测到 {status_code} 服务器异常，准备重建 Session。")
                try:
                    session.close()
                except Exception:
                    pass

                try:
                    session = rebuild_session()
                    print("Session 重建成功，等待下轮重试。")
                except Exception as rebuild_exc:
                    print(f"Session 重建失败: {rebuild_exc}")
                    traceback.print_exc()

        except Exception as exc:
            print(f"[监控] 异常: {exc}")
            traceback.print_exc()

        interrupted = interruptible_wait(state.stop_event, MONITOR_INTERVAL_SECONDS)
        if interrupted:
            break

# =========================
# cookie 优先启动
# =========================
def try_start_with_saved_cookie(state: MonitorState) -> bool:
    if not os.path.exists(COOKIE_FILE):
        return False

    session = requests.Session()
    try:
        load_cookies(session)
        watched_text, resp = fetch_page(session)

        if is_session_invalid(resp, watched_text):
            print("检测到本地 cookie 已失效。")
            return False

        print("检测到有效 cookie，直接进入监控。")
        if watched_text is not None:
            state.last_text = watched_text
            print(f"初始内容: {watched_text!r}")

        monitor_loop(session, state)
        return True

    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"cookie 校验失败，准备重新登录。原因: {exc}")
        return False


# =========================
# 主流程
# =========================
def main() -> None:
    playwright = None
    browser = None
    state = MonitorState()

    if try_start_with_saved_cookie(state):
        return

    ensure_browser_installed()

    try:
        playwright, browser, context, page = build_browser()
        wait_for_manual_login(page)
        save_cookies(context)

        print("已获取登录态，关闭浏览器，切换到 requests 轮询。")
        browser.close()
        browser = None
        playwright.stop()
        playwright = None

        session = requests.Session()
        load_cookies(session)
        monitor_loop(session, state)

    except KeyboardInterrupt:
        print("\n收到中断信号，程序退出。")
    finally:
        state.stop_event.set()

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，程序退出。")
