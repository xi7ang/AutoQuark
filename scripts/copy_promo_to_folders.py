"""Copy promotional files to each shared resource folder.

Usage example:
  python copy_promo_to_folders.py --batch-json batch_share_results.json

Configuration:
- Set QUARK_COOKIES_FILE or QUARK_PAN_TOOL_ROOT so the script can locate cookies.
- Prefer QUARK_PROMO_FOLDER_FID. If unset, QUARK_PROMO_FOLDER_PATH defaults to
  temp/要共享的文件 and will be resolved dynamically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

import httpx

from _common import get_default_cookies_file, get_quark_root, load_env_files, prepend_sys_path

load_env_files()
QUARK_ROOT = get_quark_root(require=True)
prepend_sys_path(QUARK_ROOT)

from utils import get_timestamp


class QuarkPromoCopier:
    """Copy promotional files to each shared folder."""

    def __init__(
        self,
        cookies: str,
        headers: Optional[dict] = None,
        promo_folder_fid: str = "",
        promo_folder_path: str = "",
    ):
        self.cookies = cookies
        self.headers = headers or {
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "origin": "https://pan.quark.cn",
            "referer": "https://pan.quark.cn/",
            "cookie": cookies,
        }
        self.promo_folder_fid = promo_folder_fid or os.environ.get("QUARK_PROMO_FOLDER_FID", "").strip()
        self.promo_folder_path = promo_folder_path or os.environ.get("QUARK_PROMO_FOLDER_PATH", "temp/要共享的文件").strip()

    async def get_folder_fid_by_path(self, client: httpx.AsyncClient, path: str) -> Optional[str]:
        api = "https://drive-pc.quark.cn/1/clouddrive/file/sort"
        params = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "pdir_fid": "0",
            "_page": "1",
            "_size": "100",
            "_sort": "file_type:asc,updated_at:desc",
            "__t": get_timestamp(13),
        }

        parts = [p for p in path.strip("/").split("/") if p]
        current_fid = "0"

        for part in parts:
            params["pdir_fid"] = current_fid
            resp = await client.get(api, headers=self.headers, params=params)
            data = resp.json()
            if data.get("status") != 200:
                return None
            found = None
            for item in data.get("data", {}).get("list", []):
                if item.get("file_name") == part and item.get("file_type") == "folder":
                    found = item.get("fid")
                    break
            if not found:
                return None
            current_fid = found

        return current_fid

    async def resolve_promo_folder_fid(self, client: httpx.AsyncClient) -> Optional[str]:
        if self.promo_folder_fid:
            return self.promo_folder_fid
        if not self.promo_folder_path:
            return None
        return await self.get_folder_fid_by_path(client, self.promo_folder_path)

    async def list_folder_files(self, client: httpx.AsyncClient, folder_fid: str) -> tuple[bool, Optional[list]]:
        api = "https://drive-pc.quark.cn/1/clouddrive/file/sort"
        params = {
            "pr": "ucpro",
            "fr": "pc",
            "pdir_fid": folder_fid,
            "_page": "1",
            "_size": "100",
        }

        resp = await client.get(api, headers=self.headers, params=params)
        data = resp.json()

        if data.get("status") != 200:
            return (False, None)

        if "data" in data:
            return (True, data.get("data", {}).get("list", []))

        return (False, None)

    async def copy_files(self, client: httpx.AsyncClient, file_fids: List[str], to_folder_fid: str) -> bool:
        api = "https://drive-pc.quark.cn/1/clouddrive/file/copy"
        params = {
            "pr": "ucpro",
            "fr": "pc",
        }
        data = {
            "filelist": file_fids,
            "to_pdir_fid": to_folder_fid,
        }

        resp = await client.post(api, headers=self.headers, params=params, json=data)
        result = resp.json()
        return result.get("status") == 200

    async def copy_promo_to_all_folders(self, share_results: List[dict]) -> dict:
        results = {"success": [], "skipped": [], "failed": []}

        async with httpx.AsyncClient(timeout=60.0) as client:
            promo_folder_fid = await self.resolve_promo_folder_fid(client)
            if not promo_folder_fid:
                print("[ERROR] 无法解析推广文件模板文件夹。请设置 QUARK_PROMO_FOLDER_FID 或 QUARK_PROMO_FOLDER_PATH")
                return results

            is_folder, promo_items = await self.list_folder_files(client, promo_folder_fid)
            if not is_folder or not promo_items:
                print("[ERROR] 推广文件模板文件夹为空或无法访问")
                print(f"[INFO] 当前配置: fid={promo_folder_fid!r}, path={self.promo_folder_path!r}")
                return results

            promo_fids = [f["fid"] for f in promo_items]
            print(f"[INFO] 找到 {len(promo_fids)} 个推广文件")

            for item in share_results:
                name = item.get("name", "")
                fid = item.get("fid", "")

                if not fid:
                    results["skipped"].append(name)
                    continue

                is_folder, _folder_items = await self.list_folder_files(client, fid)
                if is_folder:
                    print(f"\n[处理] {name}")
                    success = await self.copy_files(client, promo_fids, fid)
                    if success:
                        print("  ✅ 推广文件已复制到文件夹内部")
                        results["success"].append(name)
                    else:
                        print("  ❌ 复制失败")
                        results["failed"].append(name)
                else:
                    print(f"\n[跳过] {name} (不是文件夹)")
                    results["skipped"].append(name)

        return results


async def copy_promo_files(batch_json_path: str, cookies: str, headers: Optional[dict] = None) -> dict:
    batch = json.loads(Path(batch_json_path).read_text(encoding="utf-8"))
    share_results = batch.get("share_results", [])

    if not share_results:
        print("[WARN] 没有找到分享结果")
        return {"success": [], "skipped": [], "failed": []}

    print(f"[INFO] 共 {len(share_results)} 个资源需要处理")
    copier = QuarkPromoCopier(cookies, headers)
    return await copier.copy_promo_to_all_folders(share_results)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-json", required=True)
    ap.add_argument("--cookies-file", default="")
    args = ap.parse_args()

    cookies_path = get_default_cookies_file(explicit=args.cookies_file)
    if not cookies_path or not cookies_path.exists():
        print("[ERROR] Cookies 文件不存在。请设置 --cookies-file 或 QUARK_COOKIES_FILE，或者确保 QUARK_PAN_TOOL_ROOT/config/cookies.txt 存在")
        return

    cookies = cookies_path.read_text(encoding="utf-8").strip()
    results = await copy_promo_files(args.batch_json, cookies)

    print("\n[完成]")
    print(f"  ✅ 成功: {len(results['success'])}")
    print(f"  ⏭️ 跳过: {len(results['skipped'])}")
    print(f"  ❌ 失败: {len(results['failed'])}")


if __name__ == "__main__":
    asyncio.run(main())
