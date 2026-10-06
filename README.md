# MoviePilot 自研与优化插件库 (MP插件)

本项目包含为 MoviePilot (v2) 定制开发与深度调优的专属插件集合，用于无缝配合 115 网盘、OpenList STRM 媒体库及 TMDB 自动化刮削。

---

## 插件清单

### 1. `autotmdbhosts` - TMDB Hosts 自动加速与双源测速
* **功能**：
  * 后台定时（支持自定义 Cron / 默认每日凌晨 3 点）自动从多个 GitHub 源（含国内高速镜像回退）拉取最新的 TMDB IPv4 优选解析。
  * 自动在容器内执行并发延迟与丢包率测速，挑选最快可用 IP 写入 `/etc/hosts`。
  * 彻底解决 MoviePilot 在国内刮削 TMDB 时海报封面 100% 失败、连接超时的问题（海报拉取延迟从超时降低至 1.5 秒）。

### 2. `cloudoffline` - 115 云离线下载与 OpenList 302 STRM 生成增强版
* **功能与核心优化**：
  * 拦截 MoviePilot 的 `RESOURCE_DOWNLOAD` 资源下载事件，将磁力/种子推送至 115 云端离线。
  * 针对 115 云盘文件树进行了深度修复：基于 `fs_files` 真实路径面包屑（Breadcrumbs）全层级动态递归，彻底解决多层目录/伪后缀单文件导致的 STRM 路径缺失截断 Bug。
  * 自动生成带 115 真实目录结构的 `.strm` 文件，直连 OpenList 302 CDN，实现极速秒播与进度条无感拖拽。
  * 移除对已废弃模块 `bencodepy` 的依赖，全面采用 `torrentool` 规范解析 BT 种子 info_hash。

---

## 安装与同步说明

* **容器插件运行路径**：`/app/app/plugins/`
* **持久化配置挂载路径**：`/config/plugins/` (即宿主机 `/vol1/@appcenter/moviepilot/docker/moviepilot-v2/config/plugins/`)
* **迁移安装方法**：将本目录下的插件文件夹复制到宿主机 MoviePilot 的 `config/plugins/` 目录下，并重启容器即可生效。
