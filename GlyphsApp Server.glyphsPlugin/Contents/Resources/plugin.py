# encoding: utf-8
from __future__ import division, print_function, unicode_literals

###########################################################################################################
#
#   General Plugin: GlyphsApp Server
#
#   Runs a tiny local HTTP server inside Glyphs so that links in an HTML file
#   (or anywhere else) can open Edit tabs in the frontmost font.
#
#   Example link:
#       http://127.0.0.1:49152/frontmostfont/newtab/ABC
#
#   Read the docs:
#   https://github.com/mekkablue/glyphsapp-server
#   https://github.com/schriftgestalt/GlyphsSDK/tree/master/Python%20Templates/General%20Plugin
#
###########################################################################################################

import threading

import objc
from AppKit import NSMenuItem
from Foundation import NSObject

from GlyphsApp import Glyphs, Message
from GlyphsApp.plugins import GeneralPlugin

# Python 3 (Glyphs 3) ships the modern http.server module:
from http.server import BaseHTTPRequestHandler, HTTPServer
try:
	from http.server import ThreadingHTTPServer
except ImportError:  # Python < 3.7
	ThreadingHTTPServer = HTTPServer
from urllib.parse import unquote, urlparse, parse_qs

# Default port. Anything in the IANA dynamic/private range (49152–65535) is a safe choice.
DEFAULT_PORT = 49152
PORT_PREF = "com.mekkablue.GlyphsAppServer.port"

# Serialises access to the main thread so concurrent requests do not clobber each other’s result.
dispatchLock = threading.Lock()


class MainThreadDispatcher(NSObject):
	"""
	The HTTP server runs on a background thread, but every call into the Glyphs
	API must happen on the main (UI) thread. This little helper is the bridge:
	the handler asks it to run a command via performSelectorOnMainThread_…, and
	reads the outcome back from `ok` and `message` once it has finished.
	"""

	def openTab_(self, text):
		self.ok = False
		self.message = ""
		font = Glyphs.font
		if font is None:
			self.message = "No font open in Glyphs."
			return
		try:
			font.newTab(str(text))
			self.ok = True
			self.message = "Opened new tab: %s" % text
		except Exception as e:  # noqa: E722
			self.message = "Could not open tab: %s" % e


def makeHandler(dispatcher):
	"""Builds a request handler class bound to the given dispatcher."""

	class GlyphsAppRequestHandler(BaseHTTPRequestHandler):

		# ---- routing ---------------------------------------------------------

		def do_GET(self):
			parsed = urlparse(self.path)
			path = unquote(parsed.path)
			parts = [p for p in path.split("/") if p]

			# health check / discovery:
			if not parts:
				self.respond(200, "GlyphsApp Server is running.")
				return

			# expected: /<fontSelector>/<command>/<payload…>
			if len(parts) >= 2 and parts[0] == "frontmostfont" and parts[1] == "newtab":
				# text may come from the path remainder or from a ?text= query:
				query = parse_qs(parsed.query)
				if "text" in query:
					text = query["text"][0]
				else:
					text = "/".join(parts[2:])
				self.openTab(text)
				return

			self.respond(404, "Unknown command: %s" % path)

		def do_OPTIONS(self):
			# CORS preflight for fetch() calls from https pages:
			self.send_response(204)
			self.corsHeaders()
			self.end_headers()

		# ---- actions ---------------------------------------------------------

		def openTab(self, text):
			with dispatchLock:
				dispatcher.performSelectorOnMainThread_withObject_waitUntilDone_("openTab:", text, True)
				ok, message = dispatcher.ok, dispatcher.message
			self.respond(200 if ok else 500, message)

		# ---- helpers ---------------------------------------------------------

		def respond(self, code, message):
			body = message.encode("utf-8")
			self.send_response(code)
			self.send_header("Content-Type", "text/plain; charset=utf-8")
			self.send_header("Content-Length", str(len(body)))
			self.corsHeaders()
			self.end_headers()
			self.wfile.write(body)

		def corsHeaders(self):
			self.send_header("Access-Control-Allow-Origin", "*")
			self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

		def log_message(self, fmt, *args):
			# stay quiet; we do our own logging in the Macro window
			pass

	return GlyphsAppRequestHandler


class GlyphsAppServer(GeneralPlugin):

	@objc.python_method
	def settings(self):
		self.name = Glyphs.localize({
			"en": "GlyphsApp Server",
			"de": "GlyphsApp-Server",
		})
		self.httpd = None
		self.thread = None
		self.dispatcher = MainThreadDispatcher.alloc().init()

	@objc.python_method
	def start(self):
		try:
			menuItem = NSMenuItem(self.name, self.showStatus_)
			Glyphs.menu[3].append(menuItem)  # 3 == Edit menu
		except Exception as e:  # noqa: E722
			print("GlyphsApp Server: could not add menu item: %s" % e)
		self.startServer()

	@objc.python_method
	def port(self):
		port = Glyphs.defaults[PORT_PREF]
		try:
			return int(port)
		except (TypeError, ValueError):
			return DEFAULT_PORT

	@objc.python_method
	def startServer(self):
		if self.httpd is not None:
			return
		port = self.port()
		handler = makeHandler(self.dispatcher)
		try:
			self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
		except OSError as e:
			print("GlyphsApp Server: could not listen on port %i: %s" % (port, e))
			self.httpd = None
			return
		self.thread = threading.Thread(target=self.httpd.serve_forever)
		self.thread.daemon = True
		self.thread.start()
		print("GlyphsApp Server: listening on http://127.0.0.1:%i/" % port)

	@objc.python_method
	def stopServer(self):
		if self.httpd is not None:
			self.httpd.shutdown()
			self.httpd.server_close()
			self.httpd = None
			self.thread = None

	def showStatus_(self, sender):
		if self.httpd is not None:
			Message(
				title="GlyphsApp Server",
				message="Listening on http://127.0.0.1:%i/\n\nExample link:\nhttp://127.0.0.1:%i/frontmostfont/newtab/ABC" % (self.port(), self.port()),
			)
		else:
			Message(
				title="GlyphsApp Server",
				message="The server is not running. See the Macro window for details.",
			)

	@objc.python_method
	def __del__(self):
		self.stopServer()

	@objc.python_method
	def __file__(self):
		"""Please leave this method unchanged"""
		return __file__
