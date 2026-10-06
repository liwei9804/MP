"""
CloudOffline - 云盘离线下载与 STRM 自动刮削插件
支持将 MoviePilot 搜索/订阅的下载请求直接推送至 115 网盘离线，
并在离线完成后自动生成 CD2/直链 STRM 播放文件并进行影视元数据刮削。
"""
import re
import os
import time
import threading
import requests
from typing import Optional, Any, List, Dict, Tuple
from pathlib import Path
from datetime import datetime

from app.plugins import _PluginBase
from app.core.event import eventmanager, Event
from app.schemas.types import ChainEventType, NotificationType, EventType
from app.schemas import TransferInfo, MediaInfo, DownloadingTorrent, ResourceDownloadEventData
from app.schemas.file import FileItem
from app.chain.media import MediaChain
from app.chain.transfer import TransferChain
from app.log import logger

from .p115 import P115OfflineClient
from .ui import UIConfig


class CloudOffline(_PluginBase):
    # 插件元信息
    plugin_id = "CloudOffline"
    plugin_name = "云盘离线下载器"
    plugin_desc = "将搜索和订阅资源直接推送到 115 云盘离线下载，自动分类并生成 STRM 与刮削"
    plugin_version = "1.1.2"
    plugin_author = "OpenClaw"
    plugin_icon = "cloud_download.png"

    # 兼容 MoviePilot 事件系统的 name 属性
    @property
    def name(self) -> str:
        return self.plugin_name

    # 私有属性
    _enabled: bool = False
    _notify: bool = True
    _intercept_all: bool = True
    _cookies: str = ""
    _root_cid: str = "0"
    _generate_strm: bool = True
    _auto_scrape: bool = True
    _cd2_url: str = "http://192.168.31.100:8004"
    _strm_provider: str = "openlist"
    _openlist_mount_path: str = "115网盘"
    _strm_output_dir: str = "/媒体库/115电影#电影\n/媒体库/115动漫#动漫\n/媒体库/115电视剧#电视剧"
    
    _client: Optional[P115OfflineClient] = None
    _pending_tasks: List[Dict[str, Any]] = []
    _worker_thread: Optional[threading.Thread] = None
    _running: bool = False
    _lock = threading.Lock()

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        self._load_config(config)
        self._running = True
        self._start_worker()

    def _load_config(self, config: dict = None):
        """加载配置"""
        if not config:
            config = self.get_data("config") or {}
        
        self._enabled = config.get("enabled", True)
        self._notify = config.get("notify", True)
        self._intercept_all = config.get("intercept_all", True)
        self._cookies = config.get("cookies", "")
        self._root_cid = str(config.get("root_cid", "0")).strip()
        self._generate_strm = config.get("generate_strm", True)
        self._auto_scrape = config.get("auto_scrape", True)
        self._cd2_url = str(config.get("cd2_url", "http://192.168.31.100:8004")).rstrip("/")
        self._strm_output_dir = str(config.get("strm_output_dir", "/媒体库/115电影#电影\n/媒体库/115动漫#动漫\n/媒体库/115电视剧#电视剧")).rstrip("/")
        if self._cookies:
            self._client = P115OfflineClient(self._cookies)
            logger.info(f"【CloudOffline】已加载 115 离线客户端配置，根目录 CID: {self._root_cid}")
        else:
            self._client = None
            logger.warning("【CloudOffline】未配置 115 Cookies，插件处于未激活状态")

        # 加载持久化未完成任务
        saved_pending = self.get_data("pending_tasks")
        if saved_pending and isinstance(saved_pending, list):
            self._pending_tasks = saved_pending

    def get_state(self) -> bool:
        return self._enabled and bool(self._client)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return UIConfig.get_form()

    def get_page(self) -> List[dict]:
        history = self.get_data("history") or []
        return UIConfig.get_page(history)

    def stop_service(self):
        """停止服务"""
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)
        self._worker_thread = None

    def _start_worker(self):
        """启动后台轮询任务与 STRM 生成线程"""
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def _worker_loop(self):
        """后台轮询已提交的 115 离线任务"""
        while self._running:
            try:
                if self._enabled and self._client and self._generate_strm:
                    self._check_and_process_completed_tasks()
            except Exception as e:
                logger.error(f"【CloudOffline】后台处理轮询异常: {e}")
            
            # 每 30 秒轮询一次
            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1)

    def _check_and_process_completed_tasks(self):
        """检查离线任务完成状态并生成 STRM 与刮削（全自动双轨机制）"""
        if not self._client:
            return

        processed_tasks = self.get_data("processed_task_ids") or []
        processed_set = set(processed_tasks)

        with self._lock:
            current_pending = list(self._pending_tasks)

        # 获取 115 最近完成的任务列表
        tasks = self._client.get_task_list(page=1, page_size=30)
        
        # 1. 优先处理 pending_tasks
        remaining_pending = []
        for item in current_pending:
            info_hash = (item.get("info_hash") or "").lower()
            title = (item.get("title") or "").strip()
            
            matched_task = None
            for t in tasks:
                t_ih = (t.get("info_hash") or "").lower()
                t_name = t.get("name") or ""
                if (info_hash and t_ih == info_hash) or (title and (title in t_name or t_name in title)):
                    matched_task = t
                    break

            if matched_task:
                task_id = str(matched_task.get("info_hash") or matched_task.get("file_id") or matched_task.get("name"))
                if matched_task.get("status") == 2:
                    file_id = matched_task.get("file_id")
                    logger.info(f"【CloudOffline】检测到离线任务 [{title}] 已下载完成! 准备生成 STRM (file_id: {file_id})")
                    try:
                        self._process_strm_and_scrape(item, matched_task)
                        processed_set.add(task_id)
                    except Exception as ex:
                        logger.error(f"【CloudOffline】处理 STRM 与刮削失败: {ex}")
                elif matched_task.get("status") == -1:
                    logger.warning(f"【CloudOffline】离线任务 [{title}] 在 115 端标记为失败")
                    self._update_history_strm_status(title, "下载失败")
                else:
                    remaining_pending.append(item)
            else:
                remaining_pending.append(item)

        # 2. 兜底扫描：115 中已完成但未在 processed_set 中的任务
        for t in tasks:
            if t.get("status") == 2:
                task_id = str(t.get("info_hash") or t.get("file_id") or t.get("name"))
                if task_id not in processed_set:
                    t_name = t.get("name") or ""
                    logger.info(f"【CloudOffline】发现 115 已完成任务 [{t_name}]，自动触发 STRM 生成与刮削...")
                    m_info = None
                    try:
                        from app.core.metainfo import MetaInfo
                        from app.chain.media import MediaChain
                        m_info = MediaChain().recognize_by_meta(MetaInfo(title=t_name))
                    except Exception:
                        pass
                    _, cat = self._client.resolve_target_cid(self._root_cid, m_info) if m_info else (0, "电影")
                    item = {
                        "title": t_name,
                        "category": cat,
                        "media_info": None
                    }
                    try:
                        self._process_strm_and_scrape(item, t)
                        processed_set.add(task_id)
                    except Exception as ex:
                        logger.error(f"【CloudOffline】兜底处理 STRM 失败: {ex}")

        # 保存已处理 ID
        self.save_data("processed_task_ids", list(processed_set)[-200:])

        with self._lock:
            self._pending_tasks = remaining_pending
            self.save_data("pending_tasks", self._pending_tasks)

    def _process_strm_and_scrape(self, item: dict, task_info: dict):
        """提取 115 文件并生成纯 STRM 文件（无刮削、无多余通知）"""
        file_id = task_info.get("file_id")
        if not file_id:
            logger.warning("【CloudOffline】任务完成但无 file_id")
            return

        category = item.get("category", "电影")
        title = item.get("title", "")
        task_name = task_info.get("name") or title or ""
        media_info_dict = item.get("media_info") or {}

        # 遍历 115 目录或单文件
        video_files = self._collect_video_files(file_id, task_folder_name=task_name)
        if not video_files:
            logger.warning(f"【CloudOffline】在 115 file_id={file_id} 下未找到视频文件")
            return

        out_base_dir = self._get_output_dir_for_category(category)
        out_base_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"【CloudOffline】发现 {len(video_files)} 个视频文件，开始生成纯 STRM 到 {out_base_dir}")
        
        generated_strms = []
        for vf in video_files:
            fid = vf.get("fid") or vf.get("cid")
            fname = vf.get("n")
            if not fid or not fname:
                continue

            # 生成播放直链 (OpenList 或 CD2)
            if self._strm_provider == "openlist":
                strm_url = self._build_openlist_strm_url(vf, category)
                if not strm_url:
                    strm_url = f"{self._cd2_url}/play/stream/{fid}"
            else:
                strm_url = f"{self._cd2_url}/play/stream/{fid}"
            
            # 构建 STRM 保存路径
            strm_file_path = self._build_strm_path(out_base_dir, title, fname, media_info_dict)
            strm_file_path.parent.mkdir(parents=True, exist_ok=True)
            strm_file_path.write_text(strm_url, encoding="utf-8")
            generated_strms.append(strm_file_path)
            logger.info(f"【CloudOffline】已生成 STRM: {strm_file_path} -> {strm_url}")

        self._update_history_strm_status(title, f"已生成 {len(generated_strms)} 个STRM")

        # 离线完成并生成 STRM 后自动刷新 OpenList 115网盘存储缓存
        sample_rel = video_files[0].get("rel_path") or "" if video_files else ""
        self._refresh_openlist_storage(category=category, rel_path=sample_rel)

    def _collect_video_files(self, file_id: Any, task_folder_name: str = "") -> List[Dict[str, Any]]:
        """
        收集指定 115 文件/目录下的所有视频文件，
        直接利用 115 API 原生返回的完整目录层级（path breadcrumbs）获取从 115 根目录起的绝对全路径。
        """
        video_exts = {".mp4", ".mkv", ".ts", ".iso", ".mov", ".avi", ".m2ts", ".flv", ".wmv"}
        results = []
        if not self._client or not self._client.client:
            return results

        try:
            # 1. 优先按目录进行扫描 (115 离线任务完成后的 file_id 绝大多数为文件夹 CID)
            def _scan_dir(cid):
                try:
                    resp = self._client.client.fs_files({"cid": cid, "limit": 100})
                except Exception as e:
                    logger.warning(f"【CloudOffline】扫描 115 cid={cid} 异常: {e}")
                    return

                if not resp or not resp.get("state"):
                    return

                # 获取 115 原生返回的完整层级路径，例如 ["根目录", "影视", "电影", "任务文件夹名"]
                crumbs = [p.get("name") for p in resp.get("path", []) if p.get("name") and p.get("name") != "根目录"]
                dir_path = "/".join(crumbs)

                for item in resp.get("data", []):
                    if item.get("fid"):  # 文件
                        name = item.get("n", "")
                        ext = os.path.splitext(name)[1].lower()
                        if ext in video_exts:
                            full_115_path = f"{dir_path}/{name}" if dir_path else name
                            item["full_115_path"] = full_115_path
                            item["rel_path"] = full_115_path
                            results.append(item)
                    elif item.get("cid") and str(item.get("cid")) != str(cid):  # 递归扫描子目录
                        _scan_dir(item["cid"])

            _scan_dir(file_id)

            # 2. 兜底：如果扫描出来为空，说明 file_id 可能是孤立单文件
            if not results:
                try:
                    file_resp = self._client.client.fs_file(file_id)
                    if file_resp.get("state") and file_resp.get("data"):
                        f_data = file_resp["data"][0]
                        fname = f_data.get("n", "")
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in video_exts:
                            parent_crumbs = []
                            try:
                                cat_resp = self._client.client.fs_category_get(f_data.get("cid", 0))
                                if cat_resp.get("state") and cat_resp.get("data"):
                                    parent_crumbs = [p.get("file_name") for p in cat_resp["data"].get("paths", []) if p.get("file_name") and p.get("file_name") != "根目录"]
                                    if cat_resp["data"].get("file_name"):
                                        parent_crumbs.append(cat_resp["data"].get("file_name"))
                            except Exception:
                                pass
                            parent_path = "/".join(parent_crumbs)
                            full_115_path = f"{parent_path}/{fname}" if parent_path else fname
                            results.append({
                                "fid": f_data.get("fid") or file_id,
                                "n": fname,
                                "full_115_path": full_115_path,
                                "rel_path": full_115_path
                            })
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"【CloudOffline】遍历 115 视频文件异常: {e}")

        return results

    def _get_output_dir_for_category(self, category: str) -> Path:
        """根据分类解析输出目录，支持 多行 '路径#分类' 格式"""
        raw_config = str(self._strm_output_dir or "").strip()
        lines = [line.strip() for line in raw_config.splitlines() if line.strip()]
        
        category = (category or "").strip()
        category_map = {}
        first_dir = None

        for line in lines:
            if "#" in line:
                path_part, cat_part = line.split("#", 1)
                path_part = path_part.strip()
                cat_part = cat_part.strip()
                if path_part:
                    category_map[cat_part] = path_part
                    if not first_dir:
                        first_dir = path_part
            else:
                path_part = line.strip()
                if not first_dir:
                    first_dir = path_part

        # 匹配分类
        if category in category_map:
            return Path(category_map[category])
        
        # 模糊匹配（例如 动画/动漫）
        for cat_k, path_v in category_map.items():
            if cat_k in category or category in cat_k:
                return Path(path_v)

        # 兜底：如果配置了第一行
        if first_dir:
            return Path(first_dir)
        
        return Path("/strm_media") / (category or "其他")

    def _build_strm_path(self, base_dir: Path, title: str, filename: str, media_info_dict: dict) -> Path:
        """格式化输出路径"""
        media_title = media_info_dict.get("title") or title
        media_year = media_info_dict.get("year")
        folder_name = f"{media_title} ({media_year})" if media_year else media_title
        
        # 清理非法字符
        folder_name = re.sub(r'[\/:*?"<>|]', '_', folder_name)
        strm_name = os.path.splitext(filename)[0] + ".strm"
        strm_name = re.sub(r'[\/:*?"<>|]', '_', strm_name)

        # 识别是否有 Season
        season_match = re.search(r'[Ss](\d{1,2})', filename)
        if season_match:
            season_num = int(season_match.group(1))
            season_folder = f"Season {season_num}"
            return base_dir / folder_name / season_folder / strm_name

        return base_dir / folder_name / strm_name

    def _update_history_strm_status(self, title: str, strm_status: str):
        """更新历史记录中的 STRM 状态"""
        history = self.get_data("history") or []
        for h in history:
            if h.get("title") == title:
                h["strm_status"] = strm_status
                break
        self.save_data("history", history)

    @eventmanager.register(ChainEventType.ResourceDownload)
    def on_resource_download(self, event: Event):
        """拦截 MoviePilot 的下载分发"""
        if not self._enabled or not self._client:
            return

        event_data: ResourceDownloadEventData = event.event_data
        if not event_data:
            return

        context = event_data.context
        torrent_info = getattr(context, 'torrent_info', None) if context else None
        media_info = getattr(context, 'media_info', None) if context else None
        meta_info = getattr(context, 'meta_info', None) if context else None

        title = ""
        if torrent_info and torrent_info.title:
            title = torrent_info.title
        elif meta_info and meta_info.name:
            title = meta_info.name
        elif media_info and media_info.title:
            title = media_info.title
        else:
            title = "未知影片"

        enclosure = torrent_info.enclosure if torrent_info else ""
        torrent_file = getattr(context, 'torrent_file', None) if context else None

        logger.info(f"【CloudOffline】拦截到下载事件: [{title}]")

        # 准确解析分类与目标 CID
        target_cid, category_name = self._client.resolve_target_cid(self._root_cid, media_info or meta_info)

        success = False
        msg = ""
        info_hash = ""

        # 优先判断是否已经是 magnet:
        if enclosure and enclosure.startswith("magnet:"):
            logger.info(f"【CloudOffline】推送磁力链接至 115 离线: {enclosure[:60]}...")
            success, msg = self._client.add_url_task(enclosure, target_cid)
            if "xt=urn:btih:" in enclosure:
                m = re.search(r'urn:btih:([a-zA-Z0-9]+)', enclosure, re.IGNORECASE)
                if m:
                    raw_hash = m.group(1).lower()
                    if len(raw_hash) == 32: # Base32 编码
                        try:
                            import base64
                            info_hash = base64.b32decode(raw_hash.upper()).hex().lower()
                        except Exception:
                            info_hash = raw_hash
                    else:
                        info_hash = raw_hash
        else:
            # 如果是 http 种子下载链接或需要解析的链接，先在本地下载获取种子二进制内容
            actual_torrent_content = torrent_file
            if not actual_torrent_content and torrent_info:
                try:
                    from app.chain.download import DownloadChain
                    logger.info(f"【CloudOffline】正在从站点获取真实种子文件/磁力: {enclosure}")
                    t_content, _, _ = DownloadChain().download_torrent(torrent_info)
                    actual_torrent_content = t_content
                except Exception as ex:
                    logger.error(f"【CloudOffline】下载种子文件失败: {ex}")

            if isinstance(actual_torrent_content, str) and actual_torrent_content.startswith("magnet:"):
                logger.info(f"【CloudOffline】解析得到磁力链接，推送至 115: {actual_torrent_content[:60]}...")
                success, msg = self._client.add_url_task(actual_torrent_content, target_cid)
                if "xt=urn:btih:" in actual_torrent_content:
                    m = re.search(r'urn:btih:([a-zA-Z0-9]+)', actual_torrent_content, re.IGNORECASE)
                    if m:
                        raw_hash = m.group(1).lower()
                        if len(raw_hash) == 32:
                            try:
                                import base64
                                info_hash = base64.b32decode(raw_hash.upper()).hex().lower()
                            except Exception:
                                info_hash = raw_hash
                        else:
                            info_hash = raw_hash
            elif isinstance(actual_torrent_content, bytes):
                logger.info(f"【CloudOffline】获取到种子文件 ({len(actual_torrent_content)} 字节)，上传种子至 115 离线...")
                try:
                    from torrentool.torrent import Torrent
                    t = Torrent.from_string(actual_torrent_content)
                    if t.info_hash:
                        info_hash = t.info_hash.lower()
                        logger.info(f"【CloudOffline】从种子文件中提取到 info_hash: {info_hash}")
                except Exception as b_ex:
                    logger.warning(f"【CloudOffline】解析种子 info_hash 异常: {b_ex}")
                success, msg = self._client.add_torrent_task(actual_torrent_content, target_cid)
            elif enclosure and enclosure.startswith("http"):
                logger.info(f"【CloudOffline】尝试直接推送 HTTP URL 至 115: {enclosure}")
                success, msg = self._client.add_url_task(enclosure, target_cid)

        # 记录与处理
        history_item = {
            "title": title,
            "category": category_name,
            "target_cid": target_cid,
            "status": "成功" if success else "失败",
            "msg": msg,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strm_status": "等待离线完成" if success else "未生成"
        }
        self._add_history(history_item)

        if success and self._generate_strm:
            with self._lock:
                media_info_dict = {}
                if media_info:
                    media_info_dict = {
                        "title": getattr(media_info, 'title', title),
                        "year": getattr(media_info, 'year', None),
                        "type": str(getattr(media_info, 'type', '')),
                        "tmdb_id": getattr(media_info, 'tmdb_id', None)
                    }
                self._pending_tasks.append({
                    "title": title,
                    "info_hash": info_hash,
                    "category": category_name,
                    "target_cid": target_cid,
                    "media_info": media_info_dict,
                    "add_time": time.time()
                })
                self.save_data("pending_tasks", self._pending_tasks)

        if self._notify:
            self.post_message(
                mtype=NotificationType.Download if success else NotificationType.Manual,
                title="【115云盘离线】任务已接管" if success else "【115云盘离线】添加失败",
                text=f"影片：{title}\n分类：{category_name}\n结果：{msg}\n保存目录CID：{target_cid}"
            )

        if self._intercept_all:
            event_data.cancel = True
            event_data.source = "cloudoffline"
            logger.info(f"【CloudOffline】已成功接管 [{title}]，已取消本地下载链分发")

    def _add_history(self, item: dict):
        history = self.get_data("history") or []
        history.insert(0, item)
        if len(history) > 100:
            history = history[:100]
        self.save_data("history", history)


    def _refresh_openlist_storage(self, category: str = "", rel_path: str = ""):
        """刷新 OpenList 115网盘存储缓存"""
        try:
            from app.db.systemconfig_oper import SystemConfigOper
            storages = SystemConfigOper().get("Storages") or []
            openlist_base_url = "http://192.168.31.100:5244"
            openlist_token = ""
            for st in storages:
                if st.get("type") in ("alist", "openlist") or "openlist" in str(st.get("name", "")).lower():
                    cfg = st.get("config") or {}
                    if cfg.get("url"):
                        openlist_base_url = cfg.get("url").rstrip("/")
                    if cfg.get("token"):
                        openlist_token = cfg.get("token")
                    break

            mount = (self._openlist_mount_path or "115网盘").strip("/")
            headers = {"Content-Type": "application/json"}
            if openlist_token:
                headers["Authorization"] = openlist_token

            paths_to_refresh = [f"/{mount}"]
            if category:
                paths_to_refresh.append(f"/{mount}/影视/{category}")
            if rel_path:
                rel_clean = str(rel_path).lstrip("/")
                if rel_clean.startswith(mount + "/"):
                    rel_clean = rel_clean[len(mount) + 1:]
                dir_name = os.path.dirname(rel_clean)
                if dir_name:
                    paths_to_refresh.append(f"/{mount}/{dir_name}")

            for p in paths_to_refresh:
                try:
                    resp = requests.post(
                        f"{openlist_base_url}/api/fs/list",
                        headers=headers,
                        json={"path": p, "refresh": True, "page": 1, "per_page": 1},
                        timeout=10
                    )
                    logger.info(f"【CloudOffline】已刷新 OpenList 缓存: {p} -> {resp.json().get('message')}")
                except Exception as req_err:
                    logger.warning(f"【CloudOffline】刷新 OpenList 路径 [{p}] 失败: {req_err}")
        except Exception as e:
            logger.error(f"【CloudOffline】刷新 OpenList 缓存异常: {e}")

    def _build_openlist_strm_url(self, video_file: dict, category: str = "") -> Optional[str]:
        """根据 115 文件的全路径与系统 OpenList 配置，生成精确的 OpenList /d/... 直链"""
        try:
            from app.db.systemconfig_oper import SystemConfigOper
            import urllib.parse
            storages = SystemConfigOper().get("Storages") or []
            openlist_base_url = "http://192.168.31.100:5244"
            for st in storages:
                if st.get("type") in ("alist", "openlist") or "openlist" in str(st.get("name", "")).lower():
                    cfg = st.get("config") or {}
                    if cfg.get("url"):
                        openlist_base_url = cfg.get("url").rstrip("/")
                        break

            # 获取文件在 115 上的真实全路径 (从 115 根目录起的完整路径，例如 "影视/电影/任务文件夹/文件.mp4")
            full_115_path = video_file.get("full_115_path") or video_file.get("rel_path") or video_file.get("n")
            full_115_path = str(full_115_path).lstrip("/")

            # 拼接 115 挂载名（例如 "115网盘"）
            mount = str(self._openlist_mount_path or "115网盘").strip("/")

            if full_115_path.startswith(mount + "/"):
                full_alist_path = f"/{full_115_path}"
            elif full_115_path.startswith("影视/"):
                full_alist_path = f"/{mount}/{full_115_path}"
            else:
                if category and not full_115_path.startswith(category + "/"):
                    full_alist_path = f"/{mount}/影视/{category}/{full_115_path}"
                else:
                    full_alist_path = f"/{mount}/影视/{full_115_path}"

            # 对路径进行 URL 编码
            encoded_path = urllib.parse.quote(full_alist_path)
            return f"{openlist_base_url}/d{encoded_path}"
        except Exception as e:
            logger.error(f"【CloudOffline】生成 OpenList STRM 链接失败: {e}")
            return None
