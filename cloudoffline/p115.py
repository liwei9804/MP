"""
115网盘离线下载适配器
"""
import io
import time
import threading
from typing import Optional, Dict, Any, Tuple, List
from app.log import logger
from app.schemas import MediaInfo

try:
    from p115client import P115Client
    P115_AVAILABLE = True
except ImportError:
    P115_AVAILABLE = False


class P115OfflineClient:
    """115 离线下载客户端"""

    def __init__(self, cookies: str):
        self.cookies = cookies
        self.client: Optional[P115Client] = None
        self._dir_cache: Dict[str, int] = {}
        self._lock = threading.Lock()
        if P115_AVAILABLE and cookies:
            try:
                self.client = P115Client(cookies, app="web")
            except Exception as e:
                logger.error(f"115客户端初始化失败: {e}")

    def check_login(self) -> Tuple[bool, str]:
        """测试115登录状态"""
        if not self.client:
            return False, "115客户端未初始化或Cookie为空"
        try:
            user_info = self.client.user_my_info()
            if user_info.get("state"):
                uname = user_info.get('data', {}).get('uname', '115用户')
                return True, f"登录成功 ({uname})"
            return False, user_info.get("msg") or user_info.get("error") or "Cookie失效"
        except Exception as e:
            return False, str(e)

    def get_quota_info(self) -> Dict[str, Any]:
        """获取云下载配额信息"""
        if not self.client:
            return {}
        try:
            res = self.client.clouddownload_quota_info()
            if res.get("state"):
                return res.get("data") or {}
            return {}
        except Exception as e:
            logger.error(f"获取115离线配额失败: {e}")
            return {}

    def get_task_list(self, page: int = 1, page_size: int = 30) -> List[Dict[str, Any]]:
        """获取云下载任务列表"""
        if not self.client:
            return []
        try:
            res = self.client.clouddownload_task_list(page=page, page_size=page_size)
            if res.get("state"):
                return res.get("tasks") or []
            return []
        except Exception as e:
            logger.error(f"获取115离线任务列表失败: {e}")
            return []

    def get_or_create_sub_dir(self, root_cid: int, sub_dir_name: str) -> int:
        """在 root_cid 下查找或创建子目录，返回其 CID"""
        if not self.client:
            return root_cid
        if not sub_dir_name or root_cid < 0:
            return root_cid

        cache_key = f"{root_cid}:{sub_dir_name}"
        with self._lock:
            if cache_key in self._dir_cache:
                return self._dir_cache[cache_key]

        try:
            resp = self.client.fs_makedirs_app(sub_dir_name, pid=root_cid)
            if resp.get("state"):
                cid = int(resp.get("cid") or resp.get("data", {}).get("category_id") or root_cid)
                with self._lock:
                    self._dir_cache[cache_key] = cid
                return cid
            elif resp.get("errno") == 20004 or "已存在" in str(resp.get("error", "")):
                files_resp = self.client.fs_files({"cid": root_cid, "limit": 100})
                for item in files_resp.get("data", []):
                    if item.get("n") == sub_dir_name and "cid" in item:
                        cid = int(item["cid"])
                        with self._lock:
                            self._dir_cache[cache_key] = cid
                        return cid
        except Exception as e:
            logger.error(f"在115目录 {root_cid} 下创建/获取子目录 {sub_dir_name} 失败: {e}")

        return root_cid

    def resolve_target_cid(self, root_cid_str: str, media_info: Optional[Any], meta_info: Optional[Any] = None) -> Tuple[int, str]:
        """
        根据媒体类型（电影/电视剧/动漫）在根目录 CID 下自动分流，
        对于剧集/动漫（尤其是订阅中在更的剧），自动以中文剧名（如《剧名 (年份)》）在分类目录下创建并定位专属剧集文件夹，
        确保单集与整季所有下载文件均集中存放在同一个中文文件夹中。
        返回 (target_cid, category_folder)
        """
        import re
        try:
            root_cid = int(root_cid_str.strip()) if (root_cid_str and root_cid_str.strip().isdigit()) else 0
        except Exception:
            root_cid = 0

        if not media_info and not meta_info:
            return root_cid, "电影"

        mtype = getattr(media_info, 'type', None) or getattr(meta_info, 'type', None)
        mtype_val = str(getattr(mtype, 'value', mtype) or '')
        mtype_name = str(getattr(mtype, 'name', mtype) or '')
        mcategory = str(getattr(media_info, 'category', '') or '')
        genre_ids = list(getattr(media_info, 'genre_ids', []) or [])

        # 1. 判断是否为动漫
        is_anime = (
            "动漫" in mcategory or "动画" in mcategory or "Anime" in mcategory or "番" in mcategory
            or bool(getattr(media_info, 'bangumi_id', None))
            or bool(getattr(media_info, 'anidb_id', None))
            or bool(getattr(media_info, 'anilist_id', None))
            or (16 in genre_ids and ("剧" in mtype_val or "TV" in mtype_name.upper()))
        )

        if is_anime:
            category_folder = "动漫"
        elif (
            mtype_name == "MOVIE"
            or mtype_val == "电影"
            or "电影" in mtype_val
            or "影" in mtype_val
            or "电影" in mcategory
            or "Movie" in mcategory
            or "外语电影" in mcategory
            or "华语电影" in mcategory
        ):
            category_folder = "电影"
        elif (
            mtype_name == "TV"
            or mtype_val == "电视剧"
            or "电视剧" in mtype_val
            or "剧" in mtype_val
            or "电视剧" in mcategory
            or "剧集" in mcategory
            or "国产剧" in mcategory
            or "欧美剧" in mcategory
            or "韩剧" in mcategory
            or "日剧" in mcategory
            or "TV" in mcategory
        ):
            category_folder = "电视剧"
        elif 16 in genre_ids:
            category_folder = "动漫"
        else:
            category_folder = "电影"

        # 2. 提取剧集中文名称与年份（优先从 MP 订阅中匹配中文名）
        title_str = ""
        year_str = ""
        tmdb_id = getattr(media_info, 'tmdb_id', None) or getattr(meta_info, 'tmdb_id', None)
        if tmdb_id:
            try:
                from app.db.subscribe_oper import SubscribeOper
                subs = SubscribeOper().list_by_tmdbid(tmdb_id)
                if subs and subs[0].name:
                    title_str = subs[0].name.strip()
                    year_str = str(subs[0].year or '').strip()
            except Exception:
                pass

        if not title_str and media_info:
            title_str = getattr(media_info, 'title', None) or getattr(media_info, 'cn_name', None) or getattr(media_info, 'name', None) or ""
            year_str = str(getattr(media_info, 'year', '') or '')
        if not title_str and meta_info:
            title_str = getattr(meta_info, 'cn_name', None) or getattr(meta_info, 'name', None) or ""
            year_str = str(getattr(meta_info, 'year', '') or '')

        # 清理名称中的非法字符
        title_str = str(title_str).strip()
        clean_title = re.sub(r'[\/:*?"<>|]', '_', title_str).strip()

        logger.info(f"【CloudOffline】媒体 [{clean_title or '未知'}] 识别分类: 【{category_folder}】(类型:{mtype_val}, 二级分类:{mcategory}), 根目录 CID: {root_cid}")
        cat_cid = self.get_or_create_sub_dir(root_cid, category_folder)

        # 如果是电视剧或动漫，并且成功解析出了剧集中文名，创建/获取专属中文剧集目录
        if clean_title and category_folder in ("动漫", "电视剧"):
            if year_str and str(year_str).isdigit():
                series_folder_name = f"{clean_title} ({year_str})"
            else:
                series_folder_name = clean_title
            target_cid = self.get_or_create_sub_dir(cat_cid, series_folder_name)
            logger.info(f"【CloudOffline】已为剧集 [{series_folder_name}] 定位 115 专属中文目录: {category_folder}/{series_folder_name} (CID: {target_cid})")
            return target_cid, category_folder

        logger.info(f"【CloudOffline】最终推送目标目录 CID: {cat_cid} (分类: {category_folder})")
        return cat_cid, category_folder

    def add_url_task(self, url: str, target_cid: int) -> Tuple[bool, str]:
        """添加磁力链接 / HTTP 离线下载任务"""
        if not self.client:
            return False, "115客户端未就绪"
        try:
            logger.info(f"【CloudOffline】向115推送URL任务，目标CID={target_cid}, URL={url[:80]}...")
            res = self.client.clouddownload_task_add_url({"url": url, "wp_path_id": target_cid})
            if res.get("state"):
                return True, "离线任务添加成功"
            err_msg = res.get("error_msg") or res.get("msg") or res.get("error") or str(res)
            return False, f"115返回: {err_msg}"
        except Exception as e:
            return False, f"推送到115异常: {str(e)}"

    def add_torrent_task(self, torrent_bytes: bytes, target_cid: int) -> Tuple[bool, str]:
        """上传种子文件并添加云下载任务"""
        if not self.client:
            return False, "115客户端未就绪"
        try:
            logger.info(f"【CloudOffline】向115推送种子文件任务，大小={len(torrent_bytes)}字节，目标CID={target_cid}")
            from torrentool.torrent import Torrent
            t = Torrent.from_string(torrent_bytes)
            info_hash = t.info_hash
            if info_hash:
                magnet = f"magnet:?xt=urn:btih:{info_hash}"
                return self.add_url_task(magnet, target_cid)
            else:
                return False, "无法从种子中解析 info_hash"
        except Exception as e:
            return False, f"解析/推送种子异常: {str(e)}"
