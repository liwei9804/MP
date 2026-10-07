#!/bin/bash
# Backup & sync latest patched SubscribeFilesDialog into MP Git repository
docker cp moviepilot-v2:/public/assets/SubscribeFilesDialog-DOlchk9-.js /vol1/1000/项目备份/开发中/MP插件/frontend_patches/SubscribeFilesDialog.js
cd /vol1/1000/项目备份/开发中/MP插件
git add frontend_patches/
git commit -m "feat(frontend): add permanent instant cloud search button in SubscribeFilesDialog header"
git push origin main
