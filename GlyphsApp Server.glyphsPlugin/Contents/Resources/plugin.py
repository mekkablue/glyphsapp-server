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
#
###########################################################################################################

import threading

import objc
from AppKit import NSMenuItem, NSApplication, NSPasteboard, NSPasteboardTypeString, NSOnState, NSOffState
from Foundation import NSObject

from GlyphsApp import Glyphs
from GlyphsApp.plugins import GeneralPlugin

try:
	from GlyphsApp import EDIT_MENU
except ImportError:  # fall back to the fixed index of the Edit menu
	EDIT_MENU = 2

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


def setClipboard(text):
	"""Copies a string to the general pasteboard."""
	pasteboard = NSPasteboard.generalPasteboard()
	pasteboard.clearContents()
	pasteboard.setString_forType_(text, NSPasteboardTypeString)


class MainThreadDispatcher(NSObject):
	"""
	The HTTP server runs on a background thread, but every call into the Glyphs
	API must happen on the main (UI) thread. This little helper is the bridge:
	the handler asks it to run a command via performSelectorOnMainThread_…, and
	reads the outcome back from `ok` and `message` once it has finished.
	"""

	def openTab_(self, info):
		self.ok = False
		self.message = ""
		font = Glyphs.font
		if font is None:
			self.message = "No font open in Glyphs."
			return
		# `info` crosses the thread boundary as an NSDictionary, so index it
		# rather than relying on dict methods:
		text = info["text"]
		zoom = info["zoom"] if "zoom" in info else None
		try:
			font.newTab(str(text))
			# a zoom of 1000 corresponds to tab.scale = 1.0:
			if zoom is not None:
				font.currentTab.scale = zoom / 1000.0
			# bring Glyphs to the front so the new tab is visible:
			NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
			self.ok = True
			self.message = "Opened new tab: %s" % text
			if zoom is not None:
				self.message += " (zoom %g)" % zoom
		except Exception as e:  # noqa: E722
			self.message = "Could not open tab: %s" % e


def makeHandler(dispatcher):
	"""Builds a request handler class bound to the given dispatcher."""

	class GlyphsAppRequestHandler(BaseHTTPRequestHandler):

		# ---- routing ---------------------------------------------------------

		def do_GET(self):
			parsed = urlparse(self.path)
			# NB: route on the *raw* (still percent-encoded) path. If we decoded
			# first, an encoded slash (%2F) in the text — e.g. "A/A.ss01 BC" —
			# would be mistaken for a path separator. So we split the encoded
			# path, then decode only the text segment.
			rawParts = [p for p in parsed.path.split("/") if p]

			# health check / discovery:
			if not rawParts:
				self.respond(200, "GlyphsApp Server is running.")
				return

			# expected: /<fontSelector>/<command>/<payload…>
			if len(rawParts) >= 2 and rawParts[0] == "frontmostfont" and rawParts[1] == "newtab":
				# text may come from the path remainder or from a ?text= query;
				# both are URL-decoded here, so %2F becomes a literal slash:
				query = parse_qs(parsed.query)
				if "text" in query:
					text = query["text"][0]
				else:
					text = unquote("/".join(rawParts[2:]))

				# optional ?zoom= — 1000 means tab.scale = 1.0:
				zoom = None
				if "zoom" in query:
					try:
						zoom = float(query["zoom"][0])
					except ValueError:
						self.respond(400, "Invalid zoom value: %s" % query["zoom"][0])
						return
					if not (zoom > 0) or zoom == float("inf"):  # rejects NaN and infinity too
						self.respond(400, "Zoom must be a positive number, got: %s" % query["zoom"][0])
						return

				self.openTab(text, zoom)
				return

			self.respond(404, "Unknown command: %s" % unquote(parsed.path))

		def do_OPTIONS(self):
			# CORS preflight for fetch() calls from https pages:
			self.send_response(204)
			self.corsHeaders()
			self.end_headers()

		# ---- actions ---------------------------------------------------------

		def openTab(self, text, zoom=None):
			info = {"text": text}
			if zoom is not None:
				info["zoom"] = zoom
			with dispatchLock:
				dispatcher.performSelectorOnMainThread_withObject_waitUntilDone_("openTab:", info, True)
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
		self.menuItem = None
		self.dispatcher = MainThreadDispatcher.alloc().init()

	@objc.python_method
	def start(self):
		self.startServer()
		try:
			self.menuItem = NSMenuItem(self.menuTitle(), self.copyLink_)
			# a checkmark next to the item indicates that the server is running:
			self.menuItem.setState_(NSOnState if self.httpd is not None else NSOffState)
			self.menuItem.setToolTip_("Select to copy an example link to the clipboard.")
			Glyphs.menu[EDIT_MENU].append(self.menuItem)
		except Exception as e:  # noqa: E722
			print("GlyphsApp Server: could not add menu item: %s" % e)

	@objc.python_method
	def port(self):
		port = Glyphs.defaults[PORT_PREF]
		try:
			return int(port)
		except (TypeError, ValueError):
			return DEFAULT_PORT

	@objc.python_method
	def exampleLink(self):
		return "http://127.0.0.1:%i/frontmostfont/newtab/ABC" % self.port()

	@objc.python_method
	def menuTitle(self):
		if self.httpd is not None:
			return "%s: Running on Port %i" % (self.name, self.port())
		return "%s: Not Running" % self.name

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

	def copyLink_(self, sender):
		link = self.exampleLink()
		setClipboard(link)
		Glyphs.showNotification("GlyphsApp Server", "Copied to clipboard:\n%s" % link)

	@objc.python_method
	def __del__(self):
		self.stopServer()

	@objc.python_method
	def __file__(self):
		"""Please leave this method unchanged"""
		return __file__
