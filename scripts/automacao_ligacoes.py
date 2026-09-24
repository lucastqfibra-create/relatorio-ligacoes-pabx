Run python scripts/automacao_ligacoes.py
  python scripts/automacao_ligacoes.py
  shell: /usr/bin/bash -e {0}
  env:
    pythonLocation: /opt/hostedtoolcache/Python/3.10.21/x64
    PKG_CONFIG_PATH: /opt/hostedtoolcache/Python/3.10.21/x64/lib/pkgconfig
    Python_ROOT_DIR: /opt/hostedtoolcache/Python/3.10.21/x64
    Python2_ROOT_DIR: /opt/hostedtoolcache/Python/3.10.21/x64
    Python3_ROOT_DIR: /opt/hostedtoolcache/Python/3.10.21/x64
    LD_LIBRARY_PATH: /opt/hostedtoolcache/Python/3.10.21/x64/lib
    PBX_URL: ***
    PBX_USER: ***
    PBX_PASSWORD: ***
    DATA_MANUAL: 23092026
12:35:23 - [CONFIG] Utilizando data manual normalizada: 23/09/2026
12:35:23 - Iniciando rotina de gravações e transcrições do PABX...
12:35:23 - Carregando modelo Whisper 'base'...

  0%|                                               | 0.00/139M [00:00<?, ?iB/s]
 12%|████▋                                  | 16.5M/139M [00:00<00:00, 173MiB/s]
 24%|█████████▎                             | 33.0M/139M [00:00<00:00, 169MiB/s]
 35%|█████████████▊                         | 49.1M/139M [00:00<00:00, 139MiB/s]
 45%|█████████████████▋                     | 62.8M/139M [00:00<00:00, 135MiB/s]
 55%|█████████████████████▍                 | 76.0M/139M [00:00<00:00, 127MiB/s]
 65%|█████████████████████████▎             | 89.8M/139M [00:00<00:00, 132MiB/s]
 74%|█████████████████████████████▋          | 103M/139M [00:00<00:00, 106MiB/s]
 83%|█████████████████████████████████▎      | 115M/139M [00:00<00:00, 113MiB/s]
 93%|█████████████████████████████████████▏  | 129M/139M [00:01<00:00, 121MiB/s]
100%|████████████████████████████████████████| 139M/139M [00:01<00:00, 125MiB/s]
Traceback (most recent call last):
  File "/home/runner/work/relatorio-ligacoes-pabx/relatorio-ligacoes-pabx/scripts/automacao_ligacoes.py", line 741, in <module>
12:35:26 - Navegando para a página de login: http://177.10.116.84
    main()
  File "/home/runner/work/relatorio-ligacoes-pabx/relatorio-ligacoes-pabx/scripts/automacao_ligacoes.py", line 733, in main
    login_pabx(page)
  File "/home/runner/work/relatorio-ligacoes-pabx/relatorio-ligacoes-pabx/scripts/automacao_ligacoes.py", line 387, in login_pabx
    page.goto(PBX_URL, timeout=60000)
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/playwright/sync_api/_generated.py", line 8838, in goto
    self._sync(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/playwright/_impl/_sync_base.py", line 115, in _sync
    return task.result()
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/playwright/_impl/_page.py", line 524, in goto
    return await self._main_frame.goto(**locals_to_params(locals()))
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/playwright/_impl/_frame.py", line 145, in goto
    await self._channel.send("goto", locals_to_params(locals()))
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/playwright/_impl/_connection.py", line 59, in send
    return await self._connection.wrap_api_call(
  File "/opt/hostedtoolcache/Python/3.10.21/x64/lib/python3.10/site-packages/playwright/_impl/_connection.py", line 514, in wrap_api_call
    raise rewrite_error(error, f"{parsed_st['apiName']}: {error}") from None
playwright._impl._errors.TimeoutError: Page.goto: Timeout 60000ms exceeded.
Call log:
navigating to "http://177.10.116.84/", waiting until "load"

Error: Process completed with exit code 1.
