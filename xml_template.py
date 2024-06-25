feed_xml_template  = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
xmlns:podcast="https://podcastindex.org/namespace/1.0"
xmlns:atom="http://www.w3.org/2005/Atom"
xmlns:content="http://purl.org/rss/1.0/modules/content/">
$channel
</rss>
"""
channel_template = """<channel>
<atom:link href="$atom_link" rel="self" type="application/rss+xml" />
<title>$title</title>
<description>$description</description>
<link>$link/</link>
<language>zh-cn</language>
<itunes:author>$author</itunes:author>
<itunes:category text="$category" />
<itunes:explicit>false</itunes:explicit>
<itunes:image href="$image" />
$items
</channel>
"""

item_template = """<item>
<title>$title</title>
<enclosure url="$url" length="$length" type="audio/mpeg"/>
<description>$description</description>
<link>$link</link>
<itunes:duration>$duration</itunes:duration>
<itunes:image href="$image" />
<pubDate>$date</pubDate>
</item>
"""