#!/usr/bin/env python3
"""提交工作流到 ComfyUI 校验/执行，精确打印 node_errors 定位问题。
用法: python submit_check.py <workflow_api.json> [--run]
  不带 --run: 只校验(ComfyUI 仍会执行，因为 /prompt 即排队)，打印 prompt_id 或错误
"""
import json
import sys
import urllib.error
import urllib.request

COMFY = "http://127.0.0.1:8188"


def main():
    path = sys.argv[1]
    api = json.load(open(path, encoding="utf-8"))
    payload = json.dumps({"prompt": api, "client_id": "smoke"}).encode()
    req = urllib.request.Request(
        COMFY + "/prompt", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        r = urllib.request.urlopen(req, timeout=30)
        data = json.load(r)
        print("提交成功 prompt_id =", data.get("prompt_id"))
        return 0
    except urllib.error.HTTPError as e:
        d = json.loads(e.read().decode())
        print("校验失败 HTTP", e.code)
        msg = d.get("error", {}).get("message", "")
        det = d.get("error", {}).get("details", "")
        print("总错误:", msg, det)
        for nid, ne in (d.get("node_errors") or {}).items():
            ct = api.get(nid, {}).get("class_type", "?")
            print("  节点#%s %s:" % (nid, ct))
            for er in ne.get("errors", []):
                print("    - %s | %s" % (er.get("message"), er.get("details")))
        return 1


if __name__ == "__main__":
    sys.exit(main())
