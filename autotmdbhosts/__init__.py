import datetime
import re
import threading
import traceback
from typing import Any, List, Dict, Tuple, Optional
import urllib.request
import urllib.parse
import ssl

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from python_hosts import Hosts, HostsEntry

from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.utils.ip import IpUtils
from app.utils.system import SystemUtils


class AutoTmdbHosts(_PluginBase):
    # 插件名称
    plugin_name = "TMDB Hosts自动更新"
    # 插件描述
    plugin_desc = "每日自动从 CheckTMDB 等订阅源拉取最新 TMDB / Fanart 优选 IP 并更新系统 hosts，极大加速刮削与海报下载。"
    # 插件图标
    plugin_icon = "hosts.png"
    # 插件版本
    plugin_version = "1.0.1"
    # 插件作者
    plugin_author = "OpenClaw"
    # 作者主页
    author_url = "https://github.com/cnwikee/CheckTMDB"
    # 插件配置项ID前缀
    plugin_config_prefix = "autotmdbhosts_"
    # 加载顺序
    plugin_order = 11
    # 可使用的用户级别
    auth_level = 1

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    # 配置项
    _enabled: bool = False
    _notify: bool = True
    _onlyonce: bool = False
    _cron: str = "0 4 * * *"
    _url: str = "https://raw.githubusercontent.com/cnwikee/CheckTMDB/refs/heads/main/Tmdb_host_ipv4"
    _custom_hosts: str = ""
    _last_update: str = ""
    _active_hosts_count: int = 0

    def init_plugin(self, config: dict = None):
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", True)
            self._onlyonce = config.get("onlyonce", False)
            self._cron = config.get("cron", "0 4 * * *")
            self._url = config.get("url", "https://raw.githubusercontent.com/cnwikee/CheckTMDB/refs/heads/main/Tmdb_host_ipv4").strip()
            self._custom_hosts = config.get("custom_hosts", "")
            self._last_update = config.get("last_update", "")

        if self._enabled:
            # 无论是否已有上次更新记录，每次容器启动/插件初始化都立即在后台写入或拉取一次最新的 hosts
            threading.Thread(target=self._update_hosts_task, daemon=True).start()

            if self._onlyonce:
                self._onlyonce = False
                self.update_config({
                    "enabled": self._enabled,
                    "notify": self._notify,
                    "onlyonce": False,
                    "cron": self._cron,
                    "url": self._url,
                    "custom_hosts": self._custom_hosts,
                    "last_update": self._last_update
                })

            # 注册定时任务
            if self._cron:
                try:
                    self._scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Shanghai"))
                    self._scheduler.add_job(
                        self._update_hosts_task,
                        CronTrigger.from_crontab(self._cron),
                        id="autotmdbhosts_update",
                        name="TMDB Hosts自动更新",
                        max_instances=1,
                    )
                    self._scheduler.start()
                    logger.info(f"【TMDB Hosts】已启动定时更新任务，Cron: {self._cron}")
                except Exception as e:
                    logger.error(f"【TMDB Hosts】启动定时任务失败: {e}")
        else:
            self.__clear_system_hosts()

    def get_state(self) -> bool:
        return self._enabled

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '更新后发送通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '更新周期 (Cron 表达式)',
                                            'placeholder': '0 4 * * * （默认每天凌晨4点）'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'last_update',
                                            'readonly': True,
                                            'label': '上次成功更新时间',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'url',
                                            'label': 'Hosts 订阅源 URL',
                                            'placeholder': 'https://raw.githubusercontent.com/cnwikee/CheckTMDB/refs/heads/main/Tmdb_host_ipv4'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'custom_hosts',
                                            'label': '额外自定义 Hosts（可选）',
                                            'rows': 4,
                                            'placeholder': '每行一个配置，例如：\n1.1.1.1 custom.domain.com'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '本插件会自动从指定的 CheckTMDB / GitHub 优选源下载最新测速 IP 并注入容器 hosts，覆盖 TMDB API、海报 CDN (image.tmdb.org)、Fanart.tv 等，保障刮削极速响应。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cron": "0 4 * * *",
            "url": "https://raw.githubusercontent.com/cnwikee/CheckTMDB/refs/heads/main/Tmdb_host_ipv4",
            "custom_hosts": "",
            "last_update": ""
        }

    def stop_service(self):
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    @staticmethod
    def __read_system_hosts():
        if SystemUtils.is_windows():
            hosts_path = r"c:\windows\system32\drivers\etc\hosts"
        else:
            hosts_path = '/etc/hosts'
        return Hosts(path=hosts_path)

    def __clear_system_hosts(self):
        system_hosts = self.__read_system_hosts()
        orgin_entries = []
        for entry in system_hosts.entries:
            if entry.entry_type == "comment" and entry.comment == "# AutoTmdbHostsPlugin":
                break
            orgin_entries.append(entry)
        system_hosts.entries = orgin_entries
        try:
            system_hosts.write()
            logger.info("【TMDB Hosts】已清理插件写入的 hosts")
        except Exception as err:
            logger.error(f"【TMDB Hosts】恢复系统 hosts 失败：{err}")

    def _fetch_remote_hosts(self, url: str) -> List[str]:
        """拉取远程 hosts 内容，具备 GitHub raw 加速源 fallback 机制"""
        urls_to_try = [url]
        if "raw.githubusercontent.com" in url:
            raw_path = url.split("raw.githubusercontent.com")[-1]
            urls_to_try.append(f"https://ghfast.top/https://raw.githubusercontent.com{raw_path}")
            urls_to_try.append(f"https://ghproxy.net/https://raw.githubusercontent.com{raw_path}")
            urls_to_try.append("https://fastly.jsdelivr.net/gh/cnwikee/CheckTMDB@main/Tmdb_host_ipv4")

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        content = ""
        for u in urls_to_try:
            try:
                req = urllib.request.Request(
                    u,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MoviePilot/2.0"}
                )
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    if resp.status == 200:
                        content = resp.read().decode("utf-8", errors="ignore")
                        logger.info(f"【TMDB Hosts】成功从 {u} 获取最新 hosts 数据")
                        break
            except Exception as e:
                logger.warn(f"【TMDB Hosts】尝试从 {u} 获取失败: {e}")

        if not content:
            raise RuntimeError("所有订阅地址拉取均失败，请检查网络连接")

        lines = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        return lines

    def _update_hosts_task(self):
        """执行 Hosts 拉取并注入 /etc/hosts"""
        try:
            logger.info("【TMDB Hosts】开始拉取并更新优选 Hosts...")
            raw_hosts = self._fetch_remote_hosts(self._url)

            # 加上用户自定义 hosts
            if self._custom_hosts:
                for line in self._custom_hosts.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        raw_hosts.append(line)

            # 解析并写入 hosts
            system_hosts = self.__read_system_hosts()
            orgin_entries = []
            for entry in system_hosts.entries:
                if entry.entry_type == "comment" and entry.comment == "# AutoTmdbHostsPlugin":
                    break
                orgin_entries.append(entry)
            system_hosts.entries = orgin_entries

            new_entries = []
            for item in raw_hosts:
                parts = item.split()
                if len(parts) >= 2:
                    ip = parts[0]
                    domains = parts[1:]
                    entry_type = 'ipv4' if IpUtils.is_ipv4(ip) else 'ipv6'
                    new_entries.append(HostsEntry(entry_type=entry_type, address=ip, names=domains))

            if new_entries:
                system_hosts.add([HostsEntry(entry_type='comment', comment="# AutoTmdbHostsPlugin")])
                system_hosts.add(new_entries)
                system_hosts.write()

                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._last_update = now_str
                self._active_hosts_count = len(new_entries)
                logger.info(f"【TMDB Hosts】成功更新 {len(new_entries)} 条优选 Hosts 规则！")

                self.update_config({
                    "enabled": self._enabled,
                    "notify": self._notify,
                    "onlyonce": False,
                    "cron": self._cron,
                    "url": self._url,
                    "custom_hosts": self._custom_hosts,
                    "last_update": now_str
                })

                if self._notify:
                    self.post_message(
                        title="【TMDB Hosts】优选 IP 更新成功",
                        text=f"已成功从订阅源更新 {len(new_entries)} 条 Hosts 优选规则。\n更新时间：{now_str}\n涵盖：TMDB、Fanart.tv、TheTVDB 等。"
                    )
        except Exception as err:
            logger.error(f"【TMDB Hosts】更新 Hosts 失败: {err}\n{traceback.format_exc()}")
            if self._notify:
                self.post_message(
                    title="【TMDB Hosts】更新失败",
                    text=f"更新 TMDB Hosts 出现异常：{err}"
                )

    @eventmanager.register(EventType.PluginReload)
    def reload(self, event: Event):
        plugin_id = event.event_data.get("plugin_id")
        if not plugin_id or plugin_id == self.__class__.__name__:
            return self.init_plugin(self.get_config())
