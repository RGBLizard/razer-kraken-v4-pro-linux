#!/usr/bin/python3
"""Razer Control for Linux: a native window around the local panel (kraken_gui.py serves it on 127.0.0.1:8799).
Starts the panel server if it isn't running. Lives in the system tray: closing the window hides it, `--tray` starts hidden (login autostart). Needs PySide6 (system python); without it, falls back to the browser."""
import os, subprocess, sys, time, urllib.request, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://127.0.0.1:8799/"

def server_up():
    try:
        urllib.request.urlopen(URL + "api/profiles", timeout=2)
        return True
    except OSError:
        return False

def ensure_server():
    if server_up():
        return True
    subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "kraken_gui.py"), "--no-browser", "--restore"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        time.sleep(0.25)
        if server_up():
            return True
    return False

def main():
    try:
        from PySide6.QtCore import QUrl, QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        ensure_server(); webbrowser.open(URL); return
    app = QApplication(sys.argv)
    app.setApplicationName("kraken-control"); app.setDesktopFileName("kraken-control")
    sock = QLocalSocket(); sock.connectToServer("kraken-control-app")
    if sock.waitForConnected(300):  # already open: raise that window instead
        sock.write(b"raise"); sock.waitForBytesWritten(300); return
    QLocalServer.removeServer("kraken-control-app")
    single = QLocalServer(); single.listen("kraken-control-app")
    if not ensure_server():
        print("panel server did not start", file=sys.stderr); sys.exit(1)
    tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
    app.setQuitOnLastWindowClosed(not tray_ok)  # no tray: closing the window quits, as a normal app

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, kind, main_frame):  # the panel never links away; anything external opens in the browser
            if url.host() in ("127.0.0.1", "localhost") or url.scheme() in ("about", "data"):
                return True
            webbrowser.open(url.toString()); return False
    class View(QWebEngineView):
        def closeEvent(self, e):
            if tray_ok and not self.quitting:
                e.ignore(); self.hide()  # keep running in the tray
            else:
                e.accept()
    view = View()
    view.quitting = False
    view.setPage(Page(view))
    view.setWindowTitle("Razer Control")
    icon = os.path.join(HERE, "kraken-control.svg")
    if os.path.exists(icon): view.setWindowIcon(QIcon(icon))
    view.resize(1180, 860)
    view.load(QUrl(URL))
    def show_win():
        view.showNormal(); view.raise_(); view.activateWindow()
    if not (tray_ok and "--tray" in sys.argv):
        view.show()
    if tray_ok:
        tray = QSystemTrayIcon(QIcon(icon), app)
        tray.setToolTip("Razer Control")
        menu = QMenu()
        menu.addAction("Open Razer Control", show_win)
        def quit_app():
            view.quitting = True; app.quit()
        menu.addAction("Quit window (settings keep running)", quit_app)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda r: (view.hide() if view.isVisible() and not view.isMinimized() else show_win()) if r == QSystemTrayIcon.Trigger else None)
        tray.show()
    def raise_win():
        s = single.nextPendingConnection()
        if s: s.readAll()
        show_win()
    single.newConnection.connect(raise_win)
    if "--selftest" in sys.argv:  # load the page offscreen and report what rendered
        def done(ok):
            view.page().runJavaScript("document.querySelectorAll('.devc').length+' devices, tabs: '+[...document.querySelectorAll('#subtabs button')].map(b=>b.textContent)",
                                      lambda r: (print("loaded" if ok else "FAILED", r), app.quit()))
        view.loadFinished.connect(lambda ok: QTimer.singleShot(2500, lambda: done(ok)))
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
