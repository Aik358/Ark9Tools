# -*- coding: utf-8 -*-
"""PixelPainter 本地像素画历史库 / 收藏库存储。

存储约定（每个记录一个目录）：
- matrix.npy   24×24 索引矩阵（uint8/int16）
- meta.json    元数据（含 sha256 指纹，去重只读 JSON，不加载矩阵）
- preview.png  预览图

历史库（pixel_history）与收藏库（pixel_collection）使用同一 store，
两者保持物理目录分离，所有批量操作只作用于当前 store 所属的库。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image


class PixelHistoryStore:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root) if root else Path(__file__).resolve().parent / "pixel_history"
        self.root.mkdir(parents=True, exist_ok=True)
        self._fingerprints: Optional[set[str]] = None

    # =====================================================================
    # 基础读写
    # =====================================================================
    def _safe_name(self, value: str) -> str:
        value = re.sub(r"[^0-9A-Za-z一-龥._ -]+", "_", value or "未命名")
        return value.strip()[:48] or "未命名"

    @staticmethod
    def _matrix_fingerprint(matrix: np.ndarray) -> str:
        """矩阵稳定指纹：sha256(matrix.tobytes())，跨页面重复判定依据。"""
        return hashlib.sha256(np.asarray(matrix, dtype=np.int16).tobytes()).hexdigest()

    def save(self, matrix: np.ndarray, name: str = "未命名", source: str = "图片转换",
             source_path: str = "", preview_path: str = "") -> dict[str, Any]:
        matrix = np.asarray(matrix, dtype=np.int16)
        if matrix.shape != (24, 24):
            raise ValueError("历史矩阵必须是 24×24")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        item_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        folder = self.root / item_id
        folder.mkdir(parents=True, exist_ok=True)
        np.save(folder / "matrix.npy", matrix)
        meta = {
            "id": item_id,
            "fingerprint": self._matrix_fingerprint(matrix),
            "name": self._safe_name(name),
            "source": source,
            "source_path": source_path,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "preview": "preview.png",
        }
        if preview_path and Path(preview_path).is_file():
            Image.open(preview_path).convert("RGB").save(folder / "preview.png")
        else:
            self._save_preview(matrix, folder / "preview.png")
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if self._fingerprints is not None:
            self._fingerprints.add(meta["fingerprint"])
        return meta

    def _save_preview(self, matrix: np.ndarray, path: Path) -> None:
        from palette import GAME_PALETTE_DATA
        cell = 16
        image = Image.new("RGB", (24 * cell, 24 * cell), "white")
        pixels = image.load()
        for y in range(24):
            for x in range(24):
                rgb = GAME_PALETTE_DATA[int(matrix[y, x])][1]
                for py in range(y * cell, (y + 1) * cell):
                    for px in range(x * cell, (x + 1) * cell):
                        pixels[px, py] = tuple(rgb)
        image.save(path)

    def save_unique(self, matrix: np.ndarray, name: str = "未命名", source: str = "图片转换",
                    source_path: str = "") -> dict[str, Any]:
        """按元数据指纹去重保存。

        只读取各 meta.json 的 fingerprint 判断重复，绝不加载 matrix.npy。
        返回 {"saved": bool, "meta": dict|None, "fingerprint": str}。
        """
        matrix = np.asarray(matrix, dtype=np.int16)
        if matrix.shape != (24, 24):
            raise ValueError("历史矩阵必须是 24×24")
        fingerprint = self._matrix_fingerprint(matrix)
        self.ensure_fingerprints()
        if fingerprint in self._fingerprints:
            return {"saved": False, "meta": None, "fingerprint": fingerprint}
        meta = self.save(matrix, name=name, source=source, source_path=source_path)
        return {"saved": True, "meta": meta, "fingerprint": fingerprint}

    # =====================================================================
    # 元数据指纹（去重只读 JSON，不加载矩阵）
    # =====================================================================
    def ensure_fingerprints(self) -> int:
        """确保指纹集已加载，并为缺失 fingerprint 的旧记录回填一次。

        旧记录 meta.json 没有指纹字段，跨页去重会失效。这里只对缺失项
        一次性从 matrix.npy 计算并写回，之后去重全程只读 JSON。
        返回本次回填数量。
        """
        if self._fingerprints is not None:
            return 0
        filled = 0
        self._fingerprints = set()
        for meta_path in self.root.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            fp = meta.get("fingerprint")
            if not fp:
                npy = meta_path.parent / "matrix.npy"
                if not npy.is_file():
                    continue
                try:
                    matrix = np.load(npy, allow_pickle=False)
                    fp = self._matrix_fingerprint(matrix)
                    meta["fingerprint"] = fp
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
                    filled += 1
                except Exception:
                    continue
            if fp:
                self._fingerprints.add(fp)
        return filled

    def scan_meta(self) -> tuple[list[dict[str, Any]], int]:
        """读取全部记录元数据（只读 JSON），返回 (items, broken_count)。

        损坏的记录（meta.json 无法解析）跳过并计入 broken_count，
        不会让整个库加载卡死。
        """
        items: list[dict[str, Any]] = []
        broken = 0
        for meta_path in self.root.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["folder"] = str(meta_path.parent)
                meta["preview_path"] = str(meta_path.parent / meta.get("preview", "preview.png"))
                items.append(meta)
            except (OSError, ValueError, TypeError):
                broken += 1
                continue
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return items, broken

    def list_items(self) -> list[dict[str, Any]]:
        items, _ = self.scan_meta()
        return items

    def load(self, item_id: str) -> np.ndarray:
        matrix = np.load(self.root / item_id / "matrix.npy", allow_pickle=False)
        matrix = np.asarray(matrix, dtype=np.int16)
        if matrix.shape != (24, 24):
            raise ValueError("历史矩阵尺寸错误")
        return matrix

    # =====================================================================
    # 删除
    # =====================================================================
    def delete(self, item_id: str) -> None:
        folder = self.root / item_id
        if not folder.is_dir() or folder.parent != self.root:
            return
        for path in folder.iterdir():
            path.unlink(missing_ok=True)
        folder.rmdir()

    def rename(self, item_id: str, new_name: str) -> str | None:
        """重命名记录（只改 meta.json 的 name），返回新名称；失败返回 None。

        只更新元数据，不触碰 matrix.npy 与 preview.png，指纹不变。
        """
        meta_path = self.root / item_id / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["name"] = self._safe_name(new_name)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            return meta["name"]
        except (OSError, ValueError, TypeError):
            return None

    def delete_many(self, ids: list[str]) -> int:
        """批量删除：只删除当前 store（库）中存在的记录，返回删除数量。"""
        count = 0
        for item_id in ids:
            try:
                self.delete(item_id)
                count += 1
            except Exception:
                continue
        self._fingerprints = None
        return count

    def deduplicate(self) -> dict[str, int]:
        """清理当前库内指纹重复的记录（保留最早一条，删除其余）。

        兼容旧数据：早先因缺少指纹而未被去重的跨页重复记录在此统一合并。
        只对同一库生效，不影响另一个库。
        返回 {"removed": int, "kept": int}。
        """
        self.ensure_fingerprints()
        items = self.scan_meta()[0]
        seen: set[str] = set()
        removed = 0
        kept = 0
        # 按创建时间正序处理，保证保留最早的一条。
        for item in sorted(items, key=lambda i: i.get("created_at", "")):
            fp = item.get("fingerprint")
            if not fp:
                continue
            if fp in seen:
                self.delete(item["id"])
                removed += 1
            else:
                seen.add(fp)
                kept += 1
        self._fingerprints = None
        return {"removed": removed, "kept": kept}

    def migrate_source_to(self, source: str, target: "PixelHistoryStore") -> int:
        """目录级迁移旧记录，避免启动时重新加载矩阵和生成预览。"""
        moved = 0
        target_ids = {item.get("id") for item in target.list_items()}
        for item in self.list_items():
            if item.get("source") != source:
                continue
            folder = self.root / item["id"]
            destination = target.root / item["id"]
            try:
                if item["id"] in target_ids:
                    shutil.rmtree(folder, ignore_errors=True)
                elif folder.is_dir():
                    shutil.move(str(folder), str(destination))
                    moved += 1
            except OSError:
                continue
        self._fingerprints = None
        target._fingerprints = None
        return moved

    # =====================================================================
    # 批量分享 / 导出
    # =====================================================================
    def export_share(self, ids: list[str], dest_dir: str,
                     prefix: str = "pixel") -> dict[str, Any]:
        """批量导出选中的像素画到目标目录。

        每张图片导出 preview.png(.png) + matrix.npy + meta.json，
        并打包为 prefix_share.zip（文件名为 pixel_01.png 等安全命名）。
        只导出当前 store 中实际存在的记录。
        返回 {"count", "dir", "zip", "files", "failed"}。
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        items_map = {item["id"]: item for item in self.scan_meta()[0]}
        exported_files: list[Path] = []
        failed = 0
        count = 0
        for index, item_id in enumerate(ids, 1):
            meta = items_map.get(item_id)
            if meta is None:
                continue
            try:
                matrix = self.load(item_id)
            except Exception:
                failed += 1
                continue
            stem = f"{prefix}_{index:02d}"
            png = dest / f"{stem}.png"
            npy = dest / f"{stem}.npy"
            jsn = dest / f"{stem}.json"
            try:
                src_preview = meta.get("preview_path", "")
                if src_preview and Path(src_preview).is_file():
                    Image.open(src_preview).convert("RGB").save(png)
                else:
                    self._save_preview(matrix, png)
                np.save(npy, matrix)
                export_meta = {k: v for k, v in meta.items()
                               if k not in ("folder", "preview_path")}
                jsn.write_text(json.dumps(export_meta, ensure_ascii=False, indent=2),
                               encoding="utf-8")
            except Exception:
                failed += 1
                continue
            exported_files.extend([png, npy, jsn])
            count += 1

        zip_path = dest / f"{prefix}_share.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in exported_files:
                    zf.write(file, arcname=file.name)
        except Exception:
            zip_path = None

        return {
            "count": count,
            "dir": str(dest),
            "zip": str(zip_path) if zip_path else "",
            "files": len(exported_files),
            "failed": failed,
        }
