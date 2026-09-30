"""Write the Chrome enterprise policy that force-installs Observ in this image.

Force-installed extensions can't be disabled or removed by the user or the agent. Linux
is the one platform that allows force-installing a self-hosted CRX outside the Web Store.
Chromium, Chrome and Chrome for Testing read different policy directories; write all.
"""

import json
import re
import sys
from pathlib import Path

xml = Path(sys.argv[1]).read_text()
ext_id = re.search(r'appid="([a-p]{32})"', xml).group(1)
update_url = "http://127.0.0.1:7700/updates.xml"
policy = {
    "ExtensionSettings": {ext_id: {"installation_mode": "force_installed", "update_url": update_url}},
    "3rdparty": {"extensions": {ext_id: {"observd_url": "http://127.0.0.1:7777"}}},
}
for d in ("/etc/chromium/policies/managed", "/etc/opt/chrome/policies/managed",
          "/etc/opt/chrome_for_testing/policies/managed"):
    Path(d).mkdir(parents=True, exist_ok=True)
    (Path(d) / "observ.json").write_text(json.dumps(policy, indent=2))
print(f"force-install policy written for extension {ext_id}")
