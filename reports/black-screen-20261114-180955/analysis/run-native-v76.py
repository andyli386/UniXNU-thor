from pathlib import Path
import subprocess,json
root=Path('/Users/<user>/projects/thor-unixnu1030/UniXNU-pong/reports/black-screen-20261114-180955')
steps=['/tmp/thor_native_parse_v73.py', '/tmp/thor_native_facts_v73.py', '/tmp/thor_focus_chains_v73.py', '/tmp/thor_pongboot_v73.py', '/Users/<user>/projects/thor-unixnu1030/UniXNU-pong/reports/black-screen-20261114-180955/analysis/symbolize-ui18-v76.py']
results=[]
for i,p in enumerate(steps):
 r=subprocess.run(["python3",p,str(root)],capture_output=True,text=True)
 (root/"analysis"/("native-stage-"+str(i)+".log")).write_text(r.stdout+r.stderr)
 results.append({"script":p,"returncode":r.returncode})
 print(json.dumps(results[-1]),flush=True)
 if r.returncode:break
(root/"analysis/native-pipeline-result.json").write_text(json.dumps(results,indent=2))
