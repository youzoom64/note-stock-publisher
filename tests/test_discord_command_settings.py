import pytest
from PySide6.QtWidgets import QApplication,QDialog
from note_stock.discord_commands import command_names
from note_stock.discord_ui import DiscordCommandDialog
from note_stock.store import Store


@pytest.mark.parametrize('bad',['UPPER','a b','x'*33,'!'])
def test_invalid_command_name_is_rejected(bad):
    with pytest.raises(ValueError): command_names({'menu':bad})


def test_command_dialog_validates_duplicates_resets_and_persists(tmp_path):
    app=QApplication.instance() or QApplication([])
    dialog=DiscordCommandDialog(); dialog.show(); app.processEvents()
    dialog.table.item(0,1).setText('note_new')
    dialog.save.click(); app.processEvents()
    assert dialog.result()!=QDialog.DialogCode.Accepted and '同じ' in dialog.error.text()
    dialog.reset_names(); dialog.table.item(0,1).setText('/管理')
    dialog.save.click(); app.processEvents()
    assert dialog.result()==QDialog.DialogCode.Accepted
    store=Store(tmp_path); store.set_setting('discord_commands',dialog.selection())
    restored=DiscordCommandDialog(values=Store(tmp_path).setting('discord_commands'))
    restored.show(); app.processEvents()
    assert restored.table.item(0,1).text()=='管理'
    restored.close(); dialog.close()
