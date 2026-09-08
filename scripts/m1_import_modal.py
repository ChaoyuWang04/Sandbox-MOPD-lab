"""Fixed CPU-only fallback; run only after checking no matching App is active."""
import json
from pathlib import Path

import modal

REPO = Path(__file__).resolve().parents[1]
app = modal.App('sandbox-mopd-lab-m1-import')
volume = modal.Volume.from_name('sandbox-mopd-lab-m1-data-v2', create_if_missing=True, version=2)
image = (modal.Image.debian_slim(python_version='3.12')
         .add_local_dir(REPO / 'lab_runtime', '/root/lab_runtime')
         .add_local_dir(REPO / 'configs', '/root/configs')
         .add_local_file(REPO / 'environments/home5090/uv.lock', '/root/environments/home5090/uv.lock'))


@app.function(image=image, cpu=(1, 1), memory=(1024, 2048), ephemeral_disk=2048,
              max_containers=1, retries=0, timeout=1200, startup_timeout=600,
              volumes={'/vol': volume})
def import_catalog():
    from lab_runtime.modal_import import job
    return job(Path('/vol'), Path('/root/configs/m1-sources-v2.json'), volume.commit)


@app.local_entrypoint()
def main():
    print(json.dumps(import_catalog.remote(), sort_keys=True))
