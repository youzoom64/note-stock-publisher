# Third-party notices

This source distribution contains application code and dependency declarations. It does not bundle a custom executable, Python, Qt, a browser, or their binary runtime libraries. Setup can download a signed official Python installer, installs the declared packages into a local virtual environment, and downloads Chromium when Chrome/Edge is unavailable.

- Python: PSF License, https://docs.python.org/3/license.html
- PySide6 / Qt and Shiboken6: LGPLv3 and applicable Qt component licenses, https://doc.qt.io/qtforpython-6/licenses.html
- Playwright: Apache License 2.0, https://github.com/microsoft/playwright-python
- greenlet: MIT / PSF, https://github.com/python-greenlet/greenlet
- pyee: MIT, https://github.com/jfhbrook/pyee
- psutil: BSD-3-Clause, https://github.com/giampaolo/psutil
- discord.py: MIT, https://github.com/Rapptz/discord.py
- aiohttp: Apache-2.0 / MIT, https://github.com/aio-libs/aiohttp
- aiohappyeyeballs: PSF-2.0, https://github.com/aio-libs/aiohappyeyeballs
- aiosignal: Apache-2.0, https://github.com/aio-libs/aiosignal
- async-timeout: Apache-2.0, https://github.com/aio-libs/async-timeout
- attrs: MIT, https://github.com/python-attrs/attrs
- frozenlist: Apache-2.0, https://github.com/aio-libs/frozenlist
- multidict: Apache-2.0, https://github.com/aio-libs/multidict
- propcache: Apache-2.0, https://github.com/aio-libs/propcache
- yarl: Apache-2.0, https://github.com/aio-libs/yarl
- idna: BSD-3-Clause, https://github.com/kjd/idna
- typing_extensions: PSF-2.0, https://github.com/python/typing_extensions

Dependency license files are provided by the upstream packages installed by pip. Qt is dynamically linked. Corresponding Qt source and build instructions are available at https://code.qt.io/ and https://doc.qt.io/qtforpython-6/building_from_source/index.html .

This application is independent software and is not an official note product.

自動投稿のオン・オフ効果音は、フリーBGM・音楽素材MusMusのボタン音 btn06 を加工しています。 https://musmus.main.jp/
## カレンダー

- 日本の祝日・休日は[内閣府「国民の祝日について」](https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html)のCSVから取得し、日付表記と文字コードを変換しています。配布時点の収録は1955年から2027年です。
- 六曜は [kyureki-json](https://github.com/zizicici/kyureki-json) の年別旧暦データを月初の一覧に変換して算出します。著作権は zizicici に帰属し、同梱の kyureki-LICENSE.txt のMITライセンスに従います。
- 「この日の出来事・誕生日」は日本語版Wikipediaの日付ページから一部を取得し、表示から原文と編集履歴にリンクしています。Wikipediaの本文は[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)に従います。
