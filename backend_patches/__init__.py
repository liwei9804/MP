import warnings

def _filter_third_party_startup_warnings() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"invalid escape sequence",
        category=SyntaxWarning,
    )

_filter_third_party_startup_warnings()

# Patch auth_level to 2 (unlocked/authorized)
try:
    from app.helper.sites import SitesHelper
    SitesHelper.auth_level = 2
except Exception:
    pass

# Patch SiteSpider for AniBT new layout support
try:
    from app.modules.indexer.spider import SiteSpider
    orig_parse = SiteSpider.parse

    def custom_parse(self, html_text):
        if self.domain and "anibt.net" in self.domain:
            self.list = {"selector": "div[data-testid=\"release-row\"]"}
            self.fields = {
                "id": {"selector": "a[href^=\"/release/\"]", "attribute": "href", "filters": [{"name": "re_search", "args": ["rel_[^/?#]+", 0]}]},
                "title": {"selector": "h3[title]", "attribute": "title"},
                "details": {"selector": "a[href^=\"/release/\"]", "attribute": "href"},
                "download": {"selector": "button[data-testid=\"release-download\"]", "attribute": "data-release-id", "filters": [{"name": "replace", "args": ["rel_", "https://anibt.net/api/torrent/rel_"]}, {"name": "append", "args": [".torrent"]}]},
                "size": {"selector": "span.font-display", "index": 0}
            }
        return orig_parse(self, html_text)

    SiteSpider.parse = custom_parse
except Exception:
    pass


# ----------------------------------------------------
# Dynamic Indexer & Spider Integration for Custom Sites
# ----------------------------------------------------
try:
    import json
    import re
    import ssl
    import concurrent.futures
    import urllib.request
    import urllib.parse
    import xml.etree.ElementTree as ET
    from pyquery import PyQuery
    from urllib.parse import quote, urlparse
    from starlette.concurrency import run_in_threadpool
    from app.helper.sites import SitesHelper
    from app.modules.indexer.spider import SiteSpider
    from app.db.site_oper import SiteOper

    orig_get_indexers = SitesHelper.get_indexers
    orig_get_indexer = SitesHelper.get_indexer
    orig_async_get_indexers = SitesHelper.async_get_indexers
    orig_async_get_indexer = SitesHelper.async_get_indexer

    def _build_indexer_dict(s):
        return {
            "id": s.id,
            "name": s.name,
            "domain": s.domain,
            "url": s.url,
            "encoding": "UTF-8",
            "public": bool(s.public),
            "proxy": 0,
            "search": {"paths": [{"path": "", "method": "get"}]},
            "browse": {"path": ""},
            "torrents": {"list": {"selector": ""}, "fields": {}},
            "pri": s.pri or 0,
            "rss": s.rss or "",
            "cookie": s.cookie or "",
            "ua": s.ua or "",
            "apikey": s.apikey or "",
            "token": s.token or "",
            "filter": s.filter or "",
            "render": s.render or 0,
            "note": s.note or "",
            "limit_interval": s.limit_interval or 0,
            "limit_count": s.limit_count or 0,
            "limit_seconds": s.limit_seconds or 0,
            "timeout": s.timeout or 15,
            "is_active": True,
            "lst_mod_date": str(s.lst_mod_date or ""),
            "downloader": s.downloader or ""
        }

    def custom_get_indexers(self):
        indexers = orig_get_indexers(self) or []
        known_domains = {idx.get("domain") for idx in indexers if idx.get("domain")}
        known_ids = {idx.get("id") for idx in indexers if idx.get("id")}
        try:
            active_sites = SiteOper().list_active()
            for s in active_sites:
                if s.domain not in known_domains and s.id not in known_ids:
                    indexers.append(_build_indexer_dict(s))
        except Exception:
            pass
        return indexers

    def custom_get_indexer(self, domain):
        domain_str = str(domain) if domain is not None else ""
        try:
            idx = orig_get_indexer(self, domain_str)
            if idx:
                return idx
        except Exception:
            pass
        try:
            active_sites = SiteOper().list_active()
            for s in active_sites:
                if s.domain == domain_str or str(s.id) == domain_str or s.name == domain_str or (s.domain and domain_str and s.domain in domain_str):
                    return _build_indexer_dict(s)
        except Exception:
            pass
        return None

    async def custom_async_get_indexers(self):
        indexers = []
        try:
            indexers = await orig_async_get_indexers(self) or []
        except Exception:
            indexers = []
        known_domains = {idx.get("domain") for idx in indexers if idx.get("domain")}
        known_ids = {idx.get("id") for idx in indexers if idx.get("id")}
        try:
            active_sites = await SiteOper().async_list_active()
            for s in active_sites:
                if s.domain not in known_domains and s.id not in known_ids:
                    indexers.append(_build_indexer_dict(s))
        except Exception:
            pass
        return indexers

    async def custom_async_get_indexer(self, domain):
        domain_str = str(domain) if domain is not None else ""
        try:
            idx = await orig_async_get_indexer(self, domain_str)
            if idx:
                return idx
        except Exception:
            pass
        try:
            active_sites = await SiteOper().async_list_active()
            for s in active_sites:
                if s.domain == domain_str or str(s.id) == domain_str or s.name == domain_str or (s.domain and domain_str and s.domain in domain_str):
                    return _build_indexer_dict(s)
        except Exception:
            pass
        return None

    SitesHelper.get_indexers = custom_get_indexers
    SitesHelper.get_indexer = custom_get_indexer
    SitesHelper.async_get_indexers = custom_async_get_indexers
    SitesHelper.async_get_indexer = custom_async_get_indexer

    # 2. Patch SiteSpider.get_torrents & async_get_torrents
    orig_get_torrents = SiteSpider.get_torrents
    orig_async_get_torrents = SiteSpider.async_get_torrents

    def custom_get_torrents(self):
        if not self.domain:
            return orig_get_torrents(self)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

        kw = self.keyword if isinstance(self.keyword, str) else " ".join(self.keyword or [])
        if not kw:
            return []

        # 0. AniBT (anibt.net - 官方原生 Torznab API)
        if "anibt.net" in self.domain:
            search_keywords = [kw]
            try:
                from app.core.metainfo import MetaInfo
                from app.chain.media import MediaChain
                meta = MetaInfo(title=kw)
                mediainfo = MediaChain().recognize_by_meta(meta)
                if mediainfo:
                    for alias in [mediainfo.cn_name, mediainfo.en_name, mediainfo.title, mediainfo.original_title]:
                        if alias and alias not in search_keywords:
                            search_keywords.append(alias)
            except Exception:
                pass

            results = []
            seen_links = set()
            for query_kw in search_keywords:
                api_url = f"https://anibt.net/torznab/api?t=search&q={quote(query_kw)}"
                try:
                    req = urllib.request.Request(api_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=6, context=ctx) as res:
                        xml_text = res.read().decode("utf-8", errors="ignore")
                    root = ET.fromstring(xml_text)
                    items = root.findall(".//item")
                    for item in items[:int(self.result_num or 50)]:
                        title_elem = item.find("title")
                        link_elem = item.find("link")
                        comments_elem = item.find("comments")
                        pub_elem = item.find("pubDate")
                        enc_elem = item.find("enclosure")

                        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                        link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
                        enc_url = enc_elem.get("url", "").strip() if enc_elem is not None else ""
                        download_url = enc_url or link
                        page_url = comments_elem.text.strip() if comments_elem is not None and comments_elem.text else "https://anibt.net"

                        if not title or not download_url or download_url in seen_links:
                            continue
                        seen_links.add(download_url)

                        size = 0.0
                        if enc_elem is not None and enc_elem.get("length"):
                            try:
                                size = float(enc_elem.get("length"))
                            except Exception:
                                pass

                        seeders = 0
                        peers = 0
                        for attr in item.findall("{http://torznab.com/schemas/2015/feed}attr"):
                            name_att = attr.get("name")
                            val_att = attr.get("value")
                            if name_att == "seeders" and val_att:
                                try: seeders = int(val_att)
                                except Exception: pass
                            elif name_att == "peers" and val_att:
                                try: peers = int(val_att)
                                except Exception: pass
                            elif name_att == "size" and val_att and not size:
                                try: size = float(val_att)
                                except Exception: pass

                        results.append({
                            "title": title,
                            "description": f"AniBT | Seeds: {seeders}",
                            "enclosure": download_url,
                            "page_url": page_url,
                            "size": size,
                            "seeders": seeders,
                            "peers": peers,
                            "pubdate": pub_elem.text if pub_elem is not None else None
                        })
                    if results:
                        break
                except Exception:
                    continue
            return results

        # 1. 迅雷电影天堂 (xl720.com)
        if "xl720.com" in self.domain:
            search_url = f"https://www.xl720.com/?s={quote(kw)}"
            try:
                req = urllib.request.Request(search_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                doc = PyQuery(html)
                posts = doc(".post")
                detail_links = []
                for p in posts[:int(self.result_num or 8)]:
                    dp = PyQuery(p)
                    a = dp("h3 a")
                    detail_title = a.text()
                    detail_url = a.attr("href")
                    if detail_url and detail_title:
                        detail_links.append((detail_title, detail_url))

                results = []
                def fetch_xl(item):
                    d_title, d_url = item
                    sub_results = []
                    try:
                        d_req = urllib.request.Request(d_url, headers=headers)
                        with urllib.request.urlopen(d_req, timeout=4, context=ctx) as dr:
                            d_html = dr.read().decode("utf-8", errors="ignore")
                        d_doc = PyQuery(d_html)
                        seen_magnets = set()
                        for ma in d_doc("a"):
                            m_href = d_doc(ma).attr("href") or ""
                            m_text = d_doc(ma).text() or ""
                            if m_href.startswith("magnet:") and m_href not in seen_magnets:
                                seen_magnets.add(m_href)
                                title = m_text if len(m_text) > 6 and "磁力" not in m_text else d_title
                                sub_results.append({
                                    "title": title,
                                    "description": f"电影天堂 | {d_title}",
                                    "enclosure": m_href,
                                    "page_url": d_url,
                                    "size": 0.0,
                                    "seeders": 20,
                                    "peers": 10,
                                    "pubdate": None
                                })
                    except Exception:
                        pass
                    return sub_results

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(fetch_xl, item) for item in detail_links]
                    for fut in concurrent.futures.as_completed(futures):
                        results.extend(fut.result())
                return results
            except Exception:
                return []

        # 2. 电影天堂新站 (dytt8899.com)
        if "dytt8899.com" in self.domain:
            search_url = "https://www.dytt8899.com/e/search/index.php"
            try:
                data = urllib.parse.urlencode({"show": "title,smalltext", "tempid": "1", "keyboard": kw.encode("gbk", errors="ignore")}).encode("ascii")
                req = urllib.request.Request(search_url, data=data, headers={**headers, "Referer": "https://www.dytt8899.com/"})
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    html = r.read().decode("gbk", errors="ignore")
                doc = PyQuery(html)
                items = []
                for a in doc("table a"):
                    href = PyQuery(a).attr("href") or ""
                    title = PyQuery(a).text() or ""
                    if href.startswith("/i/") and title:
                        items.append((title, f"https://www.dytt8899.com{href}"))
                
                results = []
                def fetch_dytt(item):
                    t, u = item
                    sub = []
                    try:
                        r_d = urllib.request.Request(u, headers={**headers, "Referer": "https://www.dytt8899.com/"})
                        with urllib.request.urlopen(r_d, timeout=4, context=ctx) as dr:
                            d_html = dr.read().decode("gbk", errors="ignore")
                        d_doc = PyQuery(d_html)
                        seen = set()
                        for ma in d_doc("a"):
                            mh = PyQuery(ma).attr("href") or ""
                            mt = PyQuery(ma).text() or ""
                            if mh.startswith("magnet:") and mh not in seen:
                                seen.add(mh)
                                sub_t = mt if len(mt) > 6 and "磁力" not in mt else t
                                sub.append({
                                    "title": sub_t,
                                    "description": f"电影天堂DYTT | {t}",
                                    "enclosure": mh,
                                    "page_url": u,
                                    "size": 0.0,
                                    "seeders": 30,
                                    "peers": 15,
                                    "pubdate": None
                                })
                    except Exception:
                        pass
                    return sub

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(fetch_dytt, item) for item in items[:int(self.result_num or 8)]]
                    for fut in concurrent.futures.as_completed(futures):
                        results.extend(fut.result())
                return results
            except Exception:
                return []

        # 3. 6V电影网 (hao6v.cc)
        if "hao6v.cc" in self.domain:
            search_url = "https://www.hao6v.cc/e/search/index.php"
            try:
                data = urllib.parse.urlencode({"show": "title,smalltext", "tempid": "1", "tbname": "article", "keyboard": kw.encode("gbk", errors="ignore")}).encode("ascii")
                req = urllib.request.Request(search_url, data=data, headers={**headers, "Referer": "https://www.hao6v.cc/"})
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    html = r.read().decode("gbk", errors="ignore")
                doc = PyQuery(html)
                items = []
                for a in doc("#main li a, .list li a, .box li a, ul.list li a, div.main li a, table a"):
                    href = PyQuery(a).attr("href") or ""
                    title = PyQuery(a).text() or ""
                    if (href.endswith(".html") or "/dy/" in href or "/jddy/" in href or "/mj/" in href or "/gydy/" in href) and len(title) > 3:
                        full_url = href if href.startswith("http") else f"https://www.hao6v.cc{href}"
                        items.append((title, full_url))
                
                results = []
                def fetch_hao6v(item):
                    t, u = item
                    sub = []
                    try:
                        r_d = urllib.request.Request(u, headers={**headers, "Referer": "https://www.hao6v.cc/"})
                        with urllib.request.urlopen(r_d, timeout=4, context=ctx) as dr:
                            d_html = dr.read().decode("gbk", errors="ignore")
                        d_doc = PyQuery(d_html)
                        seen = set()
                        for ma in d_doc("a"):
                            mh = PyQuery(ma).attr("href") or ""
                            mt = PyQuery(ma).text() or ""
                            if mh.startswith("magnet:") and mh not in seen:
                                seen.add(mh)
                                sub_t = mt if len(mt) > 6 and "磁力" not in mt else t
                                sub.append({
                                    "title": sub_t,
                                    "description": f"6V电影 | {t}",
                                    "enclosure": mh,
                                    "page_url": u,
                                    "size": 0.0,
                                    "seeders": 25,
                                    "peers": 10,
                                    "pubdate": None
                                })
                    except Exception:
                        pass
                    return sub

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(fetch_hao6v, item) for item in items[:int(self.result_num or 8)]]
                    for fut in concurrent.futures.as_completed(futures):
                        results.extend(fut.result())
                return results
            except Exception:
                return []

        # 4. 1楼BT (1lou.xyz)
        if "1lou.xyz" in self.domain:
            search_url = f"https://www.1lou.xyz/search-{quote(kw)}.htm"
            try:
                req = urllib.request.Request(search_url, headers={**headers, "Referer": "https://www.1lou.xyz/"})
                with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                doc = PyQuery(html)
                results = []
                for t in doc("li.thread")[:int(self.result_num or 10)]:
                    dt = PyQuery(t)
                    a = dt("a[href^=\"thread-\"]")
                    title = a.text()
                    href = a.attr("href")
                    if title and href:
                        results.append({
                            "title": title,
                            "description": f"1楼BT | {title}",
                            "enclosure": f"https://www.1lou.xyz/{href}",
                            "page_url": f"https://www.1lou.xyz/{href}",
                            "size": 0.0,
                            "seeders": 15,
                            "peers": 5,
                            "pubdate": None
                        })
                return results
            except Exception:
                return []

        # 5. TorrentClaw (Torznab API 模式)
        if "torrentclaw.com" in self.domain:
            search_keywords = [kw]
            if bool(re.search(r"[一-鿿]", kw)):
                try:
                    from app.core.metainfo import MetaInfo
                    from app.chain.media import MediaChain
                    meta = MetaInfo(title=kw)
                    mediainfo = MediaChain().recognize_by_meta(meta)
                    if mediainfo and mediainfo.original_title and not bool(re.search(r"[一-鿿]", mediainfo.original_title)):
                        search_keywords.append(mediainfo.original_title)
                    elif mediainfo and mediainfo.en_title:
                        search_keywords.append(mediainfo.en_title)
                except Exception:
                    pass

            api_key = getattr(self, "apikey", None) or getattr(self, "_apikey", None) or "tc_68923c677a3358663312e8cb7ad0f83ba3d40a97ae5f4199"
            results = []
            seen_links = set()
            for query_kw in search_keywords:
                api_url = f"https://torrentclaw.com/api/v1/torznab?apikey={api_key}&t=search&q={quote(query_kw)}"
                try:
                    req = urllib.request.Request(api_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=6, context=ctx) as res:
                        xml_text = res.read().decode("utf-8", errors="ignore")
                    root = ET.fromstring(xml_text)
                    items = root.findall(".//item")
                    for item in items[:int(self.result_num or 50)]:
                        title_elem = item.find("title")
                        link_elem = item.find("link")
                        size_elem = item.find("size")
                        desc_elem = item.find("description")
                        pub_elem = item.find("pubDate")

                        title = title_elem.text if title_elem is not None else ""
                        link = link_elem.text if link_elem is not None else ""
                        if not title or not link or link in seen_links:
                            continue
                        seen_links.add(link)

                        size = 0.0
                        if size_elem is not None and size_elem.text:
                            try: size = float(size_elem.text)
                            except Exception: pass
                        
                        seeders = 0
                        peers = 0
                        for attr in item.findall("{http://torznab.com/schemas/2015/feed}attr"):
                            name_att = attr.get("name")
                            val_att = attr.get("value")
                            if name_att == "seeders" and val_att:
                                try: seeders = int(val_att)
                                except Exception: pass
                            elif name_att == "peers" and val_att:
                                try: peers = int(val_att)
                                except Exception: pass
                            elif name_att == "size" and val_att and not size:
                                try: size = float(val_att)
                                except Exception: pass

                        results.append({
                            "title": title,
                            "description": desc_elem.text if desc_elem is not None else f"TorrentClaw | Seeds: {seeders}",
                            "enclosure": link,
                            "page_url": "https://torrentclaw.com",
                            "size": size,
                            "seeders": seeders,
                            "peers": peers,
                            "pubdate": pub_elem.text if pub_elem is not None else None
                        })
                except Exception:
                    continue
            return results

                # 6. The Pirate Bay (thepiratebay.org / apibay.org API)
        if "thepiratebay.org" in self.domain or "thepiratebay" in self.domain:
            search_keywords = [kw]
            if bool(re.search(r"[一-鿿]", kw)):
                try:
                    from app.core.metainfo import MetaInfo
                    from app.chain.media import MediaChain
                    meta = MetaInfo(title=kw)
                    mediainfo = MediaChain().recognize_by_meta(meta)
                    if mediainfo and mediainfo.original_title and not bool(re.search(r"[一-鿿]", mediainfo.original_title)):
                        search_keywords.append(mediainfo.original_title)
                    elif mediainfo and mediainfo.en_title:
                        search_keywords.append(mediainfo.en_title)
                except Exception:
                    pass

            trackers = [
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.stealth.si:80/announce",
                "udp://tracker.torrent.eu.org:451/announce",
                "udp://tracker.bittor.pw:1337/announce",
                "udp://public.popcorn-tracker.org:6969/announce"
            ]
            tr_params = "".join([f"&tr={quote(tr)}" for tr in trackers])

            results = []
            seen_hashes = set()
            for query_kw in search_keywords:
                api_url = f"https://apibay.org/q.php?q={quote(query_kw)}&cat="
                try:
                    req = urllib.request.Request(api_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=6, context=ctx) as res:
                        data = json.loads(res.read().decode("utf-8", errors="ignore"))
                    if isinstance(data, list):
                        for item in data[:int(self.result_num or 50)]:
                            name = item.get("name") or ""
                            info_hash = item.get("info_hash") or ""
                            if not name or not info_hash or info_hash in seen_hashes or name == "No results were found":
                                continue
                            seen_hashes.add(info_hash)

                            size = 0.0
                            try:
                                size = float(item.get("size") or 0)
                            except Exception:
                                pass

                            seeders = 0
                            try:
                                seeders = int(item.get("seeders") or 0)
                            except Exception:
                                pass

                            peers = 0
                            try:
                                peers = int(item.get("leechers") or 0)
                            except Exception:
                                pass

                            magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}{tr_params}"

                            results.append({
                                "title": name,
                                "description": f"The Pirate Bay | Seeds: {seeders} | Leechers: {peers}",
                                "enclosure": magnet,
                                "page_url": f"https://thepiratebay.org/description.php?id={item.get('id')}",
                                "size": size,
                                "seeders": seeders,
                                "peers": peers,
                                "pubdate": None
                            })
                except Exception:
                    continue
            return results

        return orig_get_torrents(self)

    async def custom_async_get_torrents(self):
        custom_domains = ["anibt.net", "xl720.com", "dytt8899.com", "hao6v.cc", "1lou.xyz", "torrentclaw.com", "thepiratebay.org", "thepiratebay"]
        if self.domain and any(cd in self.domain for cd in custom_domains):
            return await run_in_threadpool(self.get_torrents)
        try:
            return await orig_async_get_torrents(self)
        except Exception:
            return await run_in_threadpool(self.get_torrents)

    SiteSpider.get_torrents = custom_get_torrents
    SiteSpider.async_get_torrents = custom_async_get_torrents

except Exception:
    pass
