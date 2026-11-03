#!/usr/bin/python
# coding: UTF-8

# musicdata service to read from the moOde audio REST API
from __future__ import unicode_literals

import getopt
import json
import logging
import re
import sys
import threading
import time

try:
	import urllib2
except ImportError:
	import urllib.request as urllib2

try:
	from html import unescape as unescape_html
except ImportError:
	from HTMLParser import HTMLParser
	unescape_html = HTMLParser().unescape

try:
	import Queue
except ImportError:
	import queue as Queue

try:
	import mpd
except ImportError:
	mpd = None

import musicdata


class musicdata_moode(musicdata.musicdata):

	def __init__(self, q, server=u'localhost', port=80, interval=1, mpd_server=u'localhost', mpd_port=6600, mpd_pwd=u''):
		super(musicdata_moode, self).__init__(q)

		self.server = server
		self.port = int(port)
		self.interval = float(interval)
		self.url = u"http://{0}:{1}/command/?cmd=get_currentsong".format(self.server, self.port)
		self.meta_url = u"http://{0}:{1}/command/renderer.php?cmd={2}"

		self.mpd_server = mpd_server or u'localhost'
		self.mpd_port = int(mpd_port or 6600)
		self.mpd_pwd = mpd_pwd or u''
		self.mpd = None
		self.mpd_next_attempt = 0

		self.renderer_track_key = u''
		self.cached_renderer_meta = None

		self.musicdata[u'coverurl'] = u''

		data_t = threading.Thread(target=self.run)
		data_t.daemon = True
		data_t.start()

	def tounicode(self, value):
		try:
			return value.decode()
		except AttributeError:
			return value

	def get_currentsong(self):
		try:
			response = urllib2.urlopen(self.url, timeout=5)
			try:
				raw = response.read()
			finally:
				response.close()
			return json.loads(raw)
		except Exception as e:
			logging.debug(u"Could not read moOde metadata from {0}: {1}".format(self.url, e))
			return None

	def get_renderer_meta(self, renderer):
		cmd = {
			u'airplay': u'get_aplmeta',
			u'spotify': u'get_spotmeta',
			u'qobuz': u'get_qbzmeta',
			u'bluetooth': u'get_btmeta',
		}.get(renderer.lower())
		if not cmd:
			return None

		url = self.meta_url.format(self.server, self.port, cmd)
		try:
			response = urllib2.urlopen(url, timeout=5)
			try:
				raw = response.read()
			finally:
				response.close()
			if not raw.strip():
				return None
			meta = json.loads(raw)
			return meta if isinstance(meta, dict) else None
		except Exception as e:
			logging.debug(u"Could not read moOde renderer metadata from {0}: {1}".format(url, e))
			return None

	def unescape_song(self, song):
		for key in (u'artist', u'album', u'title'):
			if song.get(key):
				song[key] = unescape_html(song[key])

	def merge_renderer_meta(self, song, meta):
		for key, metakey in (
			(u'artist', u'artist'),
			(u'album', u'album'),
			(u'title', u'title'),
			(u'coverurl', u'cover_url'),
		):
			if meta.get(metakey):
				song[key] = meta[metakey]

		if meta.get(u'sformat'):
			song[u'encoded'] = meta[u'sformat']
			bitrate = self.source_bitrate(meta[u'sformat'])
			if bitrate:
				song[u'bitrate'] = bitrate

		if not song.get(u'outrate') and meta.get(u'oformat'):
			song[u'outrate'] = meta[u'oformat']

		duration = meta.get(u'duration')
		if duration not in (None, u''):
			try:
				duration = int(float(duration))
				if duration >= 1000:
					duration = duration // 1000
				if duration > 0:
					song[u'duration'] = u"{0}".format(duration)
			except (TypeError, ValueError):
				pass

	def source_bitrate(self, sformat):
		match = re.search(r'([\d.]+)\s*(kbps|Mbps)', sformat, re.IGNORECASE)
		if match:
			return u"{0} {1}".format(match.group(1), match.group(2).lower())
		return u''

	def connect_mpd(self):
		if mpd is None:
			return None
		if self.mpd is not None:
			return self.mpd
		if time.time() < getattr(self, 'mpd_next_attempt', 0):
			return None
		client = None
		try:
			client = mpd.MPDClient(use_unicode=True)
			try:
				client.connect(self.mpd_server, self.mpd_port, timeout=5)
			except TypeError:
				client.connect(self.mpd_server, self.mpd_port)
			if self.mpd_pwd:
				client.password(self.mpd_pwd)
			self.mpd = client
			self.mpd_next_attempt = 0
		except Exception as e:
			logging.debug(u"Could not connect to MPD at {0}:{1}: {2}".format(self.mpd_server, self.mpd_port, e))
			if client is not None:
				try:
					client.disconnect()
				except Exception:
					pass
			self.mpd = None
			self.mpd_next_attempt = time.time() + 10
		return self.mpd

	def close_mpd(self):
		if self.mpd is not None:
			try:
				self.mpd.disconnect()
			except Exception:
				pass
			self.mpd = None
		self.mpd_next_attempt = time.time() + 10

	def get_mpd_status(self):
		client = self.connect_mpd()
		if client is None:
			return None
		try:
			return client.status()
		except Exception as e:
			logging.debug(u"Could not get status from MPD: {0}".format(e))
			self.close_mpd()
			return None

	def get_mpd_currentsong(self):
		client = self.connect_mpd()
		if client is None:
			return None
		try:
			return client.currentsong()
		except Exception as e:
			logging.debug(u"Could not get current song from MPD: {0}".format(e))
			self.close_mpd()
			return None

	def renderer_name(self, fileval):
		if fileval and fileval.lower().endswith(u' active'):
			return fileval[:-len(u' active')].strip()
		return u''

	def mpd_metadata(self, status, currentsong):
		song = { }
		song[u'file'] = currentsong.get(u'file', u'')
		song[u'artist'] = currentsong.get(u'artist', u'')
		song[u'album'] = currentsong.get(u'album', u'')
		song[u'title'] = currentsong.get(u'title', u'')
		song[u'track'] = currentsong.get(u'track', u'')
		song[u'state'] = status.get(u'state', u'')
		song[u'volume'] = status.get(u'volume', u'')
		song[u'bitrate'] = u"{0} kbps".format(status[u'bitrate']) if u'bitrate' in status else u''

		if u'time' in status:
			temptime = status[u'time'].split(u':')
			if len(temptime) == 2:
				song[u'elapsed'] = temptime[0]
				song[u'duration'] = temptime[1]

		if u'audio' in status:
			audio = status[u'audio'].split(u':')
			if len(audio) == 3:
				extension = song[u'file'].rsplit(u'.', 1)[-1].upper()
				song[u'encoded'] = u"{0} {1}/{2} kHz, {3}ch".format(
					extension, audio[1], round(float(audio[0]) / 1000, 1), audio[2])

		return song

	def audioinfo(self, *texts):
		bitdepth = u''
		samplerate = u''
		channels = 0

		for text in texts:
			if not text:
				continue

			if not bitdepth:
				match = re.search(r'(\d{1,2})\s*/\s*([\d.]+)', text)
				if match:
					bitdepth = u"{0} bits".format(match.group(1))
					samplerate = u"{0} kHz".format(match.group(2))

			if not samplerate:
				match = re.search(r'([\d.]+)\s*(?:kHz|K)\b', text, re.IGNORECASE)
				if match:
					samplerate = u"{0} kHz".format(match.group(1))

			if not channels:
				match = re.search(r'(\d+)\s*ch\b', text, re.IGNORECASE)
				if match:
					channels = int(match.group(1))
				elif re.search(r'\bstereo\b', text, re.IGNORECASE):
					channels = 2
				elif re.search(r'\bmono\b', text, re.IGNORECASE):
					channels = 1

		return bitdepth, samplerate, channels

	def run(self):
		logging.debug(u"moOde musicdata service starting: {0}".format(self.url))

		while True:
			try:
				song = self.get_currentsong()
				if song is None:
					song = { }
				self.status(song)
				self.sendUpdate()
			except Exception as e:
				logging.debug(u"Error in moOde run loop: {0}".format(e))
			time.sleep(self.interval)

	def sendUpdate(self):
		if self.musicdata.get(u'artist') != self.musicdata_prev.get(u'artist') or \
		   self.musicdata.get(u'title') != self.musicdata_prev.get(u'title') or \
		   self.musicdata.get(u'state') != self.musicdata_prev.get(u'state'):
			logging.info(u"moOde update: [{0}] {1} - {2} (state: {3})".format(
				self.musicdata.get(u'actPlayer', u''),
				self.musicdata.get(u'artist', u''),
				self.musicdata.get(u'title', u''),
				self.musicdata.get(u'state', u'')))
		super(musicdata_moode, self).sendUpdate()

	def status(self, song):
		fileval = song.get(u'file') or u''
		renderer = self.renderer_name(fileval)

		if renderer:
			track_key = u"{0}|{1}".format(song.get(u'artist', u''), song.get(u'title', u''))
			if track_key != getattr(self, 'renderer_track_key', u''):
				self.renderer_track_key = track_key
				self.cached_renderer_meta = self.get_renderer_meta(renderer)
			if getattr(self, 'cached_renderer_meta', None):
				self.merge_renderer_meta(song, self.cached_renderer_meta)
		else:
			self.renderer_track_key = u''
			self.cached_renderer_meta = None
			self.unescape_song(song)

		mpd_status = None
		if not renderer:
			mpd_status = self.get_mpd_status()
			if not fileval and mpd_status is not None:
				currentsong = self.get_mpd_currentsong()
				if currentsong:
					song = self.mpd_metadata(mpd_status, currentsong)
					fileval = song.get(u'file') or u''
					renderer = self.renderer_name(fileval)

		state = (song.get(u'state') or u'').lower()
		outrate = (song.get(u'outrate') or u'').lower()
		if outrate == u'not playing' or not fileval:
			state = u'stop'

		if state == u'play':
			self.musicdata[u'state'] = u'play'
		elif fileval and state not in (u'pause', u'stop'):
			self.musicdata[u'state'] = u'play'
		else:
			self.musicdata[u'state'] = u'stop'

		encoded = song.get(u'encoded') or u''
		bitrate = song.get(u'bitrate') or u''
		outrate = song.get(u'outrate') or u''
		webradio = not renderer and fileval.lower().startswith(u'http')

		self.musicdata[u'actPlayer'] = renderer if renderer else u'MPD'
		self.musicdata[u'musicdatasource'] = u'moOde'
		self.musicdata[u'artist'] = song.get(u'artist') or u''
		self.musicdata[u'album'] = song.get(u'album') or u''
		self.musicdata[u'title'] = song.get(u'title') or u''
		self.musicdata[u'uri'] = fileval
		self.musicdata[u'coverurl'] = song.get(u'coverurl') or u''
		self.musicdata[u'stream'] = u'webradio' if webradio else u'not webradio'
		self.musicdata[u'encoding'] = u'webradio' if webradio else ((encoded.split(u' ')[0].lower()) if encoded else u'')
		if bitrate.isdigit():
			bitrate = u"{0} kbps".format(bitrate)
		self.musicdata[u'bitrate'] = bitrate

		if song.get(u'volume') not in (None, u''):
			self.musicdata[u'volume'] = self.intn(song[u'volume'])
		elif mpd_status is not None and mpd_status.get(u'volume') not in (None, u''):
			self.musicdata[u'volume'] = self.intn(mpd_status[u'volume'])

		bitdepth, samplerate, channels = self.audioinfo(encoded, bitrate, outrate)
		self.musicdata[u'bitdepth'] = bitdepth
		self.musicdata[u'samplerate'] = samplerate
		self.musicdata[u'channels'] = channels
		if renderer:
			self.musicdata[u'tracktype'] = encoded if encoded else outrate
		else:
			self.musicdata[u'tracktype'] = encoded if encoded else u'MPD'

		if self.musicdata[u'state'] == u'stop':
			self.musicdata[u'elapsed'] = 0
			self.musicdata[u'length'] = 0
			self.musicdata[u'current'] = 0
			self.musicdata[u'duration'] = 0
			self.musicdata[u'remaining'] = u'0:00'
			self.musicdata[u'elapsed_formatted'] = u'0:00'
			self.musicdata[u'position'] = u'0:00'
		else:
			elapsed = self.intn(song.get(u'elapsed')) if song.get(u'elapsed') not in (None, u'') else 0
			length = self.intn(song.get(u'duration')) if song.get(u'duration') not in (None, u'') else 0

			if mpd_status is not None and mpd_status.get(u'time'):
				try:
					mpd_elapsed, mpd_length = mpd_status[u'time'].split(u':')
					if song.get(u'elapsed') in (None, u''):
						elapsed = self.intn(mpd_elapsed)
					if song.get(u'duration') in (None, u''):
						length = self.intn(mpd_length)
				except ValueError:
					pass

			self.musicdata[u'elapsed'] = elapsed
			self.musicdata[u'length'] = length
			self.musicdata[u'current'] = elapsed
			self.musicdata[u'duration'] = length

			if length > 0:
				timepos = time.strftime(u"%-M:%S", time.gmtime(elapsed)) + u"/" + time.strftime(u"%-M:%S", time.gmtime(length))
				remaining = time.strftime(u"%-M:%S", time.gmtime(length - elapsed))
			else:
				timepos = time.strftime(u"%-M:%S", time.gmtime(elapsed))
				remaining = timepos

			self.musicdata[u'remaining'] = self.tounicode(remaining)
			self.musicdata[u'elapsed_formatted'] = self.tounicode(timepos)
			self.musicdata[u'position'] = self.musicdata[u'elapsed_formatted']

		if renderer:
			self.musicdata[u'repeat'] = False
			self.musicdata[u'random'] = False
			self.musicdata[u'single'] = False
			self.musicdata[u'playlist_position'] = 0
			self.musicdata[u'playlist_length'] = 0
			self.musicdata[u'playlist_count'] = 0
			self.musicdata[u'playlist_display'] = renderer
		elif mpd_status is not None:
			self.musicdata[u'repeat'] = bool(self.intn(mpd_status.get(u'repeat', 0)))
			self.musicdata[u'random'] = bool(self.intn(mpd_status.get(u'random', 0)))
			self.musicdata[u'single'] = bool(self.intn(mpd_status.get(u'single', 0)))
			self.musicdata[u'playlist_position'] = self.intn(mpd_status.get(u'song', -1)) + 1
			self.musicdata[u'playlist_length'] = self.intn(mpd_status.get(u'playlistlength', 0))
			self.musicdata[u'playlist_count'] = self.musicdata[u'playlist_length']
			if webradio:
				self.musicdata[u'playlist_display'] = u'Radio'
			else:
				self.musicdata[u'playlist_display'] = u"{0}/{1}".format(self.musicdata[u'playlist_position'], self.musicdata[u'playlist_length'])
		else:
			self.musicdata[u'repeat'] = False
			self.musicdata[u'random'] = False
			self.musicdata[u'single'] = False
			track = self.intn(song.get(u'track'))
			self.musicdata[u'playlist_position'] = track if track > 0 else 0
			self.musicdata[u'playlist_length'] = 0
			self.musicdata[u'playlist_count'] = 0
			if webradio:
				self.musicdata[u'playlist_display'] = u'Radio'
			else:
				self.musicdata[u'playlist_display'] = renderer if renderer else u'MPD'

		self.validatemusicvars(self.musicdata)


if __name__ == u'__main__':

	logging.basicConfig(format='%(asctime)s:%(levelname)s:%(message)s', filename=u'musicdata_moode.log', level=logging.DEBUG)
	logging.getLogger().addHandler(logging.StreamHandler())

	try:
		opts, args = getopt.getopt(sys.argv[1:], u"hs:p:i:", [u"server=", u"port=", u"interval="])
	except getopt.GetoptError:
		print(u'musicdata_moode.py -s <server> -p <port> -i <interval>')
		sys.exit(2)

	server = u'localhost'
	port = 80
	interval = 1

	for opt, arg in opts:
		if opt == u'-h':
			print(u'musicdata_moode.py -s <server> -p <port> -i <interval>')
			sys.exit()
		elif opt in (u"-s", u"--server"):
			server = arg
		elif opt in (u"-p", u"--port"):
			port = arg
		elif opt in (u"-i", u"--interval"):
			interval = arg

	q = Queue.Queue()
	mdr = musicdata_moode(q, server, port, interval)

	try:
		while True:
			try:
				item = q.get(timeout=1000)
				q.task_done()
				print(u"+++++++++")
				for k, v in item.items():
					print(u"[{0}] '{1}' type {2}".format(k, v, type(v)))
				print(u"+++++++++")
				print(u"")
			except Queue.Empty:
				pass
	except KeyboardInterrupt:
		print(u'')
		pass

	print(u"Exiting...")
