"""
CloudOffline 插件 UI 渲染模块
"""
from typing import List, Dict, Any, Tuple
from datetime import datetime


class UIConfig:
    """UI配置管理类"""

    @staticmethod
    def get_page(history: List[dict] = None) -> List[dict]:
        """
        插件详情页（可视化看板与离线任务历史）
        """
        history = history or []
        total_count = len(history)
        success_count = len([h for h in history if h.get("status") == "成功"])
        fail_count = len([h for h in history if h.get("status") == "失败"])
        anime_count = len([h for h in history if h.get("category") == "动漫"])
        tv_count = len([h for h in history if h.get("category") == "电视剧"])
        movie_count = len([h for h in history if h.get("category") == "电影"])

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = len([h for h in history if h.get("time", "").startswith(today)])
        success_rate = f"{(success_count / total_count * 100):.1f}%" if total_count > 0 else "0%"

        sorted_history = sorted(history, key=lambda x: x.get('time', ''), reverse=True) if history else []

        stats_cards = {
            'component': 'VCard',
            'props': {'class': 'mb-4'},
            'content': [{
                'component': 'VCardText',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 6, 'md': 3},
                                'content': [{
                                    'component': 'VCard',
                                    'props': {'variant': 'tonal', 'color': 'primary'},
                                    'content': [{
                                        'component': 'VCardText',
                                        'props': {'class': 'text-center pa-3'},
                                        'content': [
                                            {'component': 'VIcon', 'props': {'size': 'x-large', 'class': 'mb-2'}, 'text': 'mdi-cloud-download'},
                                            {'component': 'div', 'props': {'class': 'text-h4 font-weight-bold'}, 'text': str(total_count)},
                                            {'component': 'div', 'props': {'class': 'text-caption'}, 'text': '离线总推送'}
                                        ]
                                    }]
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 6, 'md': 3},
                                'content': [{
                                    'component': 'VCard',
                                    'props': {'variant': 'tonal', 'color': 'info'},
                                    'content': [{
                                        'component': 'VCardText',
                                        'props': {'class': 'text-center pa-3'},
                                        'content': [
                                            {'component': 'VIcon', 'props': {'size': 'x-large', 'class': 'mb-2'}, 'text': 'mdi-calendar-today'},
                                            {'component': 'div', 'props': {'class': 'text-h4 font-weight-bold'}, 'text': str(today_count)},
                                            {'component': 'div', 'props': {'class': 'text-caption'}, 'text': '今日离线'}
                                        ]
                                    }]
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 6, 'md': 3},
                                'content': [{
                                    'component': 'VCard',
                                    'props': {'variant': 'tonal', 'color': 'success'},
                                    'content': [{
                                        'component': 'VCardText',
                                        'props': {'class': 'text-center pa-3'},
                                        'content': [
                                            {'component': 'VIcon', 'props': {'size': 'x-large', 'class': 'mb-2'}, 'text': 'mdi-check-circle'},
                                            {'component': 'div', 'props': {'class': 'text-h4 font-weight-bold'}, 'text': str(success_count)},
                                            {'component': 'div', 'props': {'class': 'text-caption'}, 'text': f'成功 ({success_rate})'}
                                        ]
                                    }]
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 6, 'md': 3},
                                'content': [{
                                    'component': 'VCard',
                                    'props': {'variant': 'tonal', 'color': 'warning'},
                                    'content': [{
                                        'component': 'VCardText',
                                        'props': {'class': 'text-center pa-3'},
                                        'content': [
                                            {'component': 'VIcon', 'props': {'size': 'x-large', 'class': 'mb-2'}, 'text': 'mdi-folder-star-multiple'},
                                            {'component': 'div', 'props': {'class': 'text-h6 font-weight-bold'}, 'text': f"影{movie_count} 剧{tv_count} 漫{anime_count}"},
                                            {'component': 'div', 'props': {'class': 'text-caption'}, 'text': '智能分流统计'}
                                        ]
                                    }]
                                }]
                            }
                        ]
                    }
                ]
            }]
        }

        table_items = []
        for item in sorted_history[:50]:
            status_color = "success" if item.get("status") == "成功" else "error"
            cat = item.get("category", "其他")
            cat_color = "purple" if cat == "动漫" else ("blue" if cat == "电视剧" else "teal")
            strm_status = item.get("strm_status", "")
            strm_tag = f" <span class='text-caption text-primary'>[{strm_status}]</span>" if strm_status else ""
            table_items.append({
                "title": item.get("title", "-") + strm_tag,
                "category": f"<span class='text-{cat_color} font-weight-bold'>{cat}</span>",
                "target": f"CID: {item.get('target_cid', 0)}",
                "time": item.get("time", "-"),
                "status": f"<span class='text-{status_color}'>{item.get('status', '-')}</span>",
                "msg": item.get("msg", "-")
            })

        table_card = {
            'component': 'VCard',
            'props': {'class': 'mb-4'},
            'content': [
                {
                    'component': 'VCardTitle',
                    'text': '最近云盘离线 & STRM 记录'
                },
                {
                    'component': 'VDataTable',
                    'props': {
                        'headers': [
                            {'title': '时间', 'key': 'time', 'width': '160px'},
                            {'title': '影片名称', 'key': 'title'},
                            {'title': '智能分类', 'key': 'category', 'width': '100px'},
                            {'title': '目标云端', 'key': 'target', 'width': '120px'},
                            {'title': '状态', 'key': 'status', 'width': '90px'},
                            {'title': '反馈信息', 'key': 'msg'}
                        ],
                        'items': table_items,
                        'no-data-text': '暂无云盘离线记录'
                    }
                }
            ]
        }

        return [stats_cards, table_card]

    @staticmethod
    def get_form() -> Tuple[List[dict], Dict[str, Any]]:
        """
        获取插件配置表单
        """
        form_schema = [
            {
                'component': 'VForm',
                'content': [
                    # 基本开关
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6},
                             'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用云盘离线下载'}}]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6},
                             'content': [{'component': 'VSwitch', 'props': {'model': 'intercept_all', 'label': '拦截所有下载请求 (绕过本地QB/TR)'}}]}
                        ]
                    },

                    # 115 配置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cookies',
                                        'label': '115 账号 Cookie',
                                        'placeholder': 'UID=...; CID=...; SEID=...',
                                        'hint': '填入 115 网页端或客户端导出的 Cookie 字符串',
                                        'persistent-hint': True,
                                        'type': 'password'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'root_cid',
                                        'label': '115 保存根目录 CID',
                                        'placeholder': '0 (0 代表网盘根目录，或填入指定文件夹 CID)',
                                        'hint': '插件将在该根目录下自动创建并分流到【电影】/【电视剧】/【动漫】',
                                        'persistent-hint': True
                                    }
                                }]
                            }
                        ]
                    },

                    # 分隔线：STRM 生成
                    {
                        'component': 'VDivider',
                        'props': {'class': 'my-3'}
                    },

                    # STRM 功能开关与配置
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 12},
                             'content': [{'component': 'VSwitch', 'props': {'model': 'generate_strm', 'label': '离线完成后自动生成纯 STRM 文件 (供外部插件/媒体库自行刮削)'}}]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'strm_provider',
                                        'label': 'STRM 直链生成方式',
                                        'items': [
                                            {'title': 'CloudDrive2 (CD2 直链)', 'value': 'cd2'},
                                            {'title': 'OpenList / AList (直接读取系统存储配置)', 'value': 'openlist'}
                                        ],
                                        'hint': '选择 STRM 内部 URL 的生成服务源',
                                        'persistent-hint': True
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cd2_url',
                                        'label': 'CD2 直链地址 (CD2模式生效)',
                                        'placeholder': 'http://192.168.31.100:8004',
                                        'hint': '例如 http://192.168.31.100:8004/play/stream/<fid>',
                                        'persistent-hint': True
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'openlist_mount_path',
                                        'label': 'OpenList 115网盘挂载名 (OpenList模式生效)',
                                        'placeholder': '115网盘',
                                        'hint': 'OpenList中挂载115的文件夹名称（如 115网盘）',
                                        'persistent-hint': True
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 12},
                                'content': [{
                                    'component': 'VTextarea',
                                    'props': {
                                        'model': 'strm_output_dir',
                                        'label': 'STRM 本地输出目录配置',
                                        'rows': 4,
                                        'placeholder': '/媒体库/115电影#电影\n/媒体库/115动漫#动漫\n/媒体库/115电视剧#电视剧',
                                        'hint': '支持多行配置分类目录，格式：路径#分类（例如：/媒体库/115动漫#动漫）。若未匹配分类则默认使用第一行目录',
                                        'persistent-hint': True
                                    }
                                }]
                            }
                        ]
                    }
                ]
            }
        ]

        default_config = {
            "enabled": True,
            "intercept_all": True,
            "cookies": "",
            "root_cid": "0",
            "generate_strm": True,
            "strm_provider": "openlist",
            "cd2_url": "http://192.168.31.100:8004",
            "openlist_mount_path": "115网盘",
            "strm_output_dir": "/媒体库/115电影#电影\n/媒体库/115动漫#动漫\n/媒体库/115电视剧#电视剧"
        }

        return form_schema, default_config
