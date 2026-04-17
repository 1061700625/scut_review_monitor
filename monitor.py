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
PORTAL_URL = "https://yjsjw-443.webvpn.scut.edu.cn/"  # "https://yjsjw.scut.edu.cn/"
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

NOTIFY_URL = "http://14.103.144.178:7790/send/friend"
NOTIFY_TARGET = "1061700625"
NOTIFY_KEY = "xxx"

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
    page.wait_for_selector(f"text={SUCCESS_TEXT}", timeout=0)
    print("检测到登录成功。")


# =========================
# cookie 相关
# =========================
def sync_cookies(context, session: requests.Session) -> None:
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
