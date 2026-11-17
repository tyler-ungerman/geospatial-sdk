import json
from base.datasources.http import HttpResponse

def response(obj, status=200):
    return HttpResponse(status, json.dumps(obj).encode())

USGS = {
    "features": [
        {"properties": {"mag": 6.2, "place": "Near coast, Japan", "tsunami": 1, "time": 123, "url": "https://usgs.test/1"}, "geometry": {"coordinates": [140, 36, 10.3]}},
        {"properties": {"mag": 3.5, "place": "Utah, United States", "tsunami": 0}, "geometry": {"coordinates": [-111.1, 40.3, 2]}},
        {"properties": {"mag": 4}, "geometry": {"coordinates": [500, 40, 0]}},
    ],
}
FIRMS = "latitude,longitude,brightness,confidence,acq_date,acq_time,frp\n1.50,3.20,350,98,2026-11-17,0009,800\n4.00,5.00,301,90,2026-11-17,1244,75\n6.00,7.00,333,20,2026-11-17,1115,2000\n"
GDACS = {"features": [{"properties": {"alertlevel": "Red", "eventtype": "TC", "name": "Fictional Storm", "country": "Philippines", "eventid": 19, "episodeid": 4, "population": {"value": 1234}}, "geometry": {"coordinates": [122.2, 14.8]}}]}
OPENSKY = {"time": 1700000000, "states": [
    ["ABC123", "RCH234  ", "United States", None, None, 22.4, 33.1, 1000, False, 90, 200],
    ["DEF456", "EZ234   ", "United Kingdom", None, None, 25.4, 31.1, None, True, 200, 180],
    ["AAA111", None, "Unknown", None, None, 51.2, 47.4, None, False, None, None],
]}
RSS = """<rss><channel><item><title><![CDATA[Major &amp; urgent news]]></title><link>https://example.test/a</link><pubDate>Tue, 17 Nov 2026 10:00:00 GMT</pubDate><source url="https://example.test">Example News</source></item><item><title>Google News</title></item></channel></rss>"""
GDELT = {"articles": [{"title": "Global alert", "url": "https://example.test/g", "domain": "example.test", "seendate": "20261117T100000Z"}, {"title": "GLOBAL ALERT", "url": "https://example.test/dup"}]}
AI_BATCH = {"choices": [{"message": {"content": "```json\n[{\"index\":0,\"category\":\"military\",\"severity\":\"high\",\"country\":\"France\",\"lat\":0,\"lng\":2.4,\"summary\":\"Test\",\"is_breaking\":true}]\n```"}}]}
AI_SINGLE = {"choices": [{"message": {"tool_calls": [{"function": {"arguments": '{"category":"disaster","severity":"high","country":"France","lat":42,"lng":3,"summary":"A fictional event"}'}}]}}]}


class FakeTransport:
    def __init__(self, *, failure=None):
        self.urls = []
        self.failure = failure

    def request(self, method, url, *, headers=None, json_body=None, timeout=15):
        self.urls.append((method, url, headers, json_body, timeout))
        if self.failure and self.failure in url:
            return HttpResponse(429, b"Too Many Requests")
        if "earthquake.usgs.gov" in url:
            return response(USGS)
        if "firms.modaps.eosdis.nasa.gov" in url:
            return HttpResponse(200, FIRMS.encode())
        if "gdacs.org" in url:
            return response(GDACS)
        if "opensky-network.org" in url:
            return response(OPENSKY)
        if "news.google.com" in url:
            return HttpResponse(200, RSS.encode())
        if "gdeltproject.org" in url:
            return response(GDELT)
        if "ai.gateway.lovable.dev" in url:
            return response(AI_SINGLE if (json_body or {}).get("tools") else AI_BATCH)
        raise AssertionError(f"Unrecognized URL: {url}")


