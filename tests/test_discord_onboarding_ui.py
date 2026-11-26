from note_stock.qt import QApplication,QDialog
from note_stock.discord_ui import DiscordSetupDialog,DiscordPanel
from note_stock.store import Store


def test_onboarding_choices_back_blank_name_and_cancel():
    app=QApplication.instance() or QApplication([])
    dialog=DiscordSetupDialog()
    assert not dialog.next.isEnabled()
    assert 'Discord連携しますか？' in dialog.pages.widget(0).layout().itemAt(0).widget().text()
    assert 'iPhoneやスマホを使用して外出先から操作できます' in dialog.pages.widget(0).layout().itemAt(1).widget().text()
    dialog.use.click(); dialog.next.click()
    assert dialog.pages.currentIndex()==1 and not dialog.next.isEnabled()
    dialog.new.click(); dialog.name.clear()
    assert not dialog.next.isEnabled()
    dialog.name.setText('My server')
    assert not dialog.next.isEnabled()
    dialog.guidelines.click()
    assert not dialog.next.isEnabled()
    dialog.developer_terms.click()
    assert dialog.next.isEnabled()
    dialog.back.click(); dialog.skip.click(); dialog.next.click()
    assert dialog.result()==QDialog.DialogCode.Accepted
    assert not dialog.selection()['enabled']
    canceled=DiscordSetupDialog(); canceled.reject()
    assert canceled.result()==QDialog.DialogCode.Rejected
    existing=DiscordSetupDialog(); existing.use.click(); existing.next.click(); existing.existing.click()
    existing.developer_terms.click()
    assert existing.next.isEnabled() and not existing.name.isEnabled()


def test_manual_opens_html_and_switches_to_automatic_from_the_guide():
    from note_stock.qt import QPoint,Qt
    from note_stock.qt import QTest
    from note_stock.qt import QTextBrowser

    app=QApplication.instance() or QApplication([])
    dialog=DiscordSetupDialog()
    dialog.show()
    dialog.manual.click()
    app.processEvents()
    assert dialog.manual_guide.isVisible()
    assert dialog.pages.currentIndex()==2 and not dialog.next.isEnabled()
    browser=dialog.manual_guide.findChild(QTextBrowser)
    assert 'チャンネルの管理' in browser.toPlainText()
    target=next(QPoint(x,y) for y in range(110) for x in range(15,340,5)
                if browser.anchorAt(QPoint(x,y))=='note-stock:automatic')
    QTest.mouseClick(browser.viewport(),Qt.MouseButton.LeftButton,pos=target)
    assert not dialog.manual_guide.isVisible()
    assert dialog.pages.currentIndex()==1 and dialog.use.isChecked()
    assert not dialog.next.isEnabled()
    dialog.close()


def test_skip_and_cancel_start_no_worker_and_skip_survives_restart(tmp_path,monkeypatch):
    from note_stock.qt import QWidget
    app=QApplication.instance() or QApplication([])
    class Window(QWidget):
        def __init__(self):
            super().__init__(); self.store=Store(tmp_path)
        def notify(self,*args): pass
    window=Window(); panel=DiscordPanel(window,connect_on_start=False)
    monkeypatch.setattr(DiscordSetupDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    panel.setup()
    assert window.store.setting('discord_onboarding',None) is None and panel.setup_worker is None
    monkeypatch.setattr(DiscordSetupDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    monkeypatch.setattr(DiscordSetupDialog,'selection',lambda self:{'enabled':False,'destination':'existing','server_name':'note管理'})
    panel.setup()
    assert panel.setup_worker is None and panel.bot is None
    assert Store(tmp_path).setting('discord_enabled') is False
    panel.poll.stop(); panel.shutdown()


def test_checkbox_opens_same_setup_when_unconfigured_and_reconnects_when_configured(tmp_path,monkeypatch):
    from note_stock.qt import QWidget
    app=QApplication.instance() or QApplication([])
    class Window(QWidget):
        def __init__(self):
            super().__init__(); self.store=Store(tmp_path)
        def notify(self,*args): pass
    window=Window(); panel=DiscordPanel(window,connect_on_start=False)
    called=[]
    monkeypatch.setattr(DiscordSetupDialog,'exec',lambda self:(called.append('same-dialog') or QDialog.DialogCode.Rejected))
    panel.enabled.click()
    assert called==['same-dialog'] and not panel.enabled.isChecked()
    window.store.set_setting('discord_token','encrypted-placeholder')
    window.store.set_setting('discord',{'guild':'123456789012345678'})
    monkeypatch.setattr(panel,'connect_bot',lambda:called.append('connect'))
    panel.enabled.click()
    assert called==['same-dialog','connect'] and window.store.setting('discord_enabled')
    panel.enabled.click()
    assert not Store(tmp_path).setting('discord_enabled') and panel.bot is None
    panel.poll.stop(); panel.shutdown()


def test_discord_cli_status_never_discloses_secrets(tmp_path):
    from note_stock.commands import Commands
    store=Store(tmp_path)
    store.set_setting('discord_token','never-disclose')
    store.set_setting('discord',{'guild':'123456789012345678','channel':'234567890123456789'})
    result=Commands(store).handle({'command':'discord'})
    assert result['ok'] and result['result']['configured']
    assert 'never-disclose' not in str(result)
    assert not Commands(store).handle({'command':'discord','data':{'action':'setup'}})['ok']


def test_connection_buttons_show_actual_in_progress_state(tmp_path,monkeypatch):
    from note_stock.qt import QWidget
    from types import SimpleNamespace

    app=QApplication.instance() or QApplication([])
    window=QWidget()
    window.store=Store(tmp_path)
    window.store.set_setting('discord_token','placeholder')
    window.notify=lambda *_:None
    panel=DiscordPanel(window,connect_on_start=False)
    class Bot:
        alive=False
        stopped=0
        def start(self): self.alive=True
        def is_alive(self): return self.alive
        def stop(self): self.stopped+=1
    monkeypatch.setattr('note_stock.discord_bot.BotThread',lambda store:Bot())
    window.show()
    panel.show()
    app.processEvents()
    panel.connect_button.click()
    assert panel.connect_button.text()=='接続中…'
    window.store.set_setting('discord_connection','管理メニュー準備完了')
    panel.update_connection_action()
    assert panel.connect_button.text()=='接続・再接続'
    original=panel.bot
    panel.connect_button.click()
    assert panel.connect_button.text()=='再接続中…' and original.stopped==1
    original.alive=False
    panel.update_connection_action()
    assert panel.connect_button.text()=='接続中…' and panel.bot is not original
    panel.disconnect_button.click()
    assert panel.disconnect_button.text()=='切断中…'
    panel.bot.alive=False
    panel.update_connection_action()
    assert panel.disconnect_button.text()=='Discord接続を停止'
    assert window.store.setting('discord_connection')=='停止'
    panel.poll.stop()
    window.close()
