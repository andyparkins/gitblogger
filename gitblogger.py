#!/usr/bin/python
# ----------------------------------------------------------------------------
# Project: gitblogger
#
# Version Control
#    $Author$
#      $Date$
#        $Id$
#
# Legal
#    Copyright 2010  Andy Parkins
#
# Notes
#   http://code.google.com/apis/blogger/docs/2.0/developers_guide_protocol.html
#
#   apt-get install python-httplib2 python-markdown python-xml
#
# ----------------------------------------------------------------------------

# ----- Includes

# Standard library
import sys
import os
import subprocess
import locale
import time
import datetime
import re
from optparse import OptionParser

# Additional
import markdown
import httplib2
from xml.dom import minidom


# ----- Constants


# ----- Class definitions

#
# Class:
# Description:
#
class Record:
	pass

#
# Class:
# Description:
#
class TGBError(Exception):
	pass

#
# Class:	TGitBlogger
# Description:
#
class TGitBlogger:
	#
	# Function:		__init__
	# Description:
	#
	def __init__( self, argv ):
		self.argv = argv

		# Load the options record with default values
		self.options = Record()
		self.options.username = None
		self.options.password = None
		self.options.targetblog = None
		self.options.mode = 'post-receive'
		self.options.draft = False

	#
	# Function:		run
	# Description:
	#
	def run( self ):
		self.readConfigFile()
		self.readCommandLine()

		if self.options.verbose:
			print >> sys.stderr,  " --- Verbose mode active"
			print >> sys.stderr,  self

		# Create a httplib object for doing the web work
		self.http = httplib2.Http()

		if self.options.mode == 'post-receive':
			print >> sys.stderr, "gitblogger: Running in git post-receive hook mode; reading changes from stdin..."
			while True:
				line = sys.stdin.readline().strip()
				if not line:
					break
				# 309e0dcfb0d92a7b746e85e48377b2cc2a9025cb 12f575ad888d05919463b9ae501978eb79c091b7 refs/heads/master
				line = line.split(' ')
				if len(line) < 3:
					continue
				(oldrev, newrev, refname) = tuple(line)

				# refname will be refs/heads/blogbranch
				refname = refname.split('/',3)
				if refname[0] != 'refs' or refname[1] != 'heads':
					continue

				# Find the matching blog record
				for blog in self.gitblogs.iterkeys():
					if self.gitblogs[blog]['blogbranch'] != refname[2]:
						continue
					self.sendBlogUpdate( oldrev, newrev, blog )

		elif self.options.mode == 'commandline':
			oldrev = self.positionalparameters[0]
			newrev = self.positionalparameters[1]
			refname = self.positionalparameters[2]

			# refname will be refs/heads/blogbranch
			refname = refname.split('/',3)
			if refname[0] != 'refs' or refname[1] != 'heads':
				return

			# Find the matching blog record
			for blog in self.gitblogs.iterkeys():
				if self.gitblogs[blog]['blogbranch'] != refname[2]:
					continue
				self.sendBlogUpdate( oldrev, newrev, blog )

	#
	# Function:		sendBlogUpdate
	# Description:
	#
	def sendBlogUpdate( self, oldrev, newrev, blogname ):
		blog = self.gitblogs[blogname]

		difftree = subprocess.Popen(["git", "diff-tree", "-r", "-M", "-C", \
			"--relative=%s" % blog['repositorypath'],
			oldrev, newrev], stdout=subprocess.PIPE).communicate()[0]

		if self.options.verbose:
			print >> sys.stderr, difftree
		difftree = difftree.strip().split('\n')

		if len(difftree) == 0:
			return

		# Establish authentication token
		print "gitblogger: Logging into Google GData API as", self.options.username
		self.authtoken = self.authenticate( self.options.username, self.options.password )
		if self.authtoken is None:
			raise TGBError("GData authentication failed")
		print "gitblogger: Success, authtoken is",self.authtoken

		print "gitblogger: Fetching details of blogs owned by", self.options.username
		self.fetchBlogDetails()

		for change in difftree:
			change = change.split(' ', 5)
			status = change[4].split('\t')

			fromhash = change[2]
			tohash = change[3]

			print "gitblogger: ---",status[1]
			while True: 
				if status[0][0] == 'A':
					print "gitblogger: Fetching new article from repository,",status[1]
					md_source = subprocess.Popen(["git", "cat-file", "-p", \
						tohash], stdout=subprocess.PIPE).communicate()[0]
					print "gitblogger: Converting %d byte article to XHTML" % (len(md_source))
					# Install plugin handler for different file types
					# here.  At the moment this is hard coded for
					# ikiwiki-style markdown
					try:
						(atom, meta) = self.ikiwikiToAtom(md_source)
					except Exception, e:
						raise TGBError("Couldn't convert article to XHTML: %s" % (e.args[0]) )
					print "gitblogger: Converted article, \"%s\", is %d bytes, uploading..." % (meta.title, len(atom))
					if self.options.preview:
						print atom
						break
					id = self.createPost( atom, meta, blogname )
					print "gitblogger: Upload complete, article was assigned the id, \"%s\"" % (id)
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"add", "-f", "-m", id, tohash], stdout=subprocess.PIPE)
					if retcode != 0:
						print "gitblogger: Failed to record tracking ID in git repository"

				elif status[0][0] == 'C':
					print "gitblogger: Article copied %s -> %s" % (status[1], status[2])

				elif status[0][0] == 'D':
					print "gitblogger: Article deleted", status[1]
					print "gitblogger: Looking up corresponding blog post tracking ID"
					gitproc = subprocess.Popen(["git", "notes", "--ref", self.notesref, \
						"show", fromhash], stdout=subprocess.PIPE)
					postid = gitproc.communicate()[0].strip()
					retcode = gitproc.wait()
					if retcode != 0:
						print "gitblogger: Lookup failed, can't delete remote blog article without a tracking ID"
						break
					print "gitblogger: Removing remote posting with tracking ID,",postid
					self.deletePost( postid )
					print "gitblogger: Removing local copy of tracking ID"
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"remove", fromhash], stdout=subprocess.PIPE)

				elif status[0][0] == 'M':
					print "gitblogger: Article modified", status[1]
					print "gitblogger: Copying tracking ID from %s -> %s" % (fromhash, tohash)
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"copy", "-f", fromhash, tohash], \
						stdout=subprocess.PIPE)
					if retcode != 0:
						print "gitblogger: Couldn't copy tracking ID, treating article modification as article creation"
						status[0] = 'A'
						continue
					print "gitblogger: Fetching replacement article from repository,",status[1]
					md_source = subprocess.Popen(["git", "cat-file", "-p", \
						tohash], stdout=subprocess.PIPE).communicate()[0]
					print "gitblogger: Converting %d byte article to XHTML" % (len(md_source))
					# Install plugin handler for different file types
					# here.  At the moment this is hard coded for
					# ikiwiki-style markdown
					try:
						(atom, meta) = self.ikiwikiToAtom(md_source)
					except Exception, e:
						raise TGBError("Couldn't convert article to XHTML: %s" % (e.args[0]) )
					print "gitblogger: Converted article, \"%s\", is %d bytes, uploading..." % (meta.title, len(atom))
					if self.options.preview:
						print atom
						break
					# Fetch postid
					gitproc = subprocess.Popen(["git", "notes", "--ref", self.notesref, \
						"show", tohash], stdout=subprocess.PIPE)
					postid = gitproc.communicate()[0].strip()
					retcode = gitproc.wait()
					if retcode != 0:
						print "gitblogger: Lookup failed, can't delete remote blog article without a tracking ID"
						break
					print "gitblogger: Modifying remote post,", postid
					self.modifyPost( atom, meta, postid )
					print "gitblogger: Post modified"

				elif status[0][0] == 'R':
					print "gitblogger: Article renamed %s -> %s" % (status[1], status[2])
					print "gitblogger: Updating local post ID tracking information"
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"copy", "-f", fromhash, tohash], \
						stdout=subprocess.PIPE)
					if retcode != 0:
						print "gitblogger: Couldn't copy tracking ID, treating article modification as article creation"
						status = ['A', status[2]]
						continue
					if status[0][1:] != '100':
						print "gitblogger: Converting rename with change to a modify for",status[1]
						status = ['M', status[2]]
						continue

				elif status[0][0] == 'T':
					print "gitblogger: Ignoring change of file type for", status[1]

				elif status[0][0] == 'U':
					raise TGBError("'Unmerged' change code from git-diff-tree")

				elif status[0][0] == 'X':
					raise TGBError("Unknown change code from git-diff-tree")

				break

	#
	# Function:		fetchPostDetails
	# Description:
	#
	def fetchPostDetails( self, targetblog ):
		if self.authtoken:
			headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2'
			}
		else:
			headers = None

		blog = self.Blogs[targetblog]

		response, content = self.http.request(blog.PostURL + '?max-results=9999',
			'GET', headers=headers)
		if response['status'] == '404':
			raise TUPError(content)
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TUPError(content)

		# --- Parse
		try:
			dom = minidom.parseString( content )
		except:
			print >> sys.stderr,  content
			raise
		feedNode = dom.getElementsByTagName("feed")[0]
		entryNodes = feedNode.getElementsByTagName("entry")

		self.Posts = dict()
		for blog in entryNodes:
			PostRecord = Record()
			PostRecord.id = self.XMLText(blog.getElementsByTagName("id")[0])
			PostRecord.title = self.XMLText(blog.getElementsByTagName("title")[0])
			links = blog.getElementsByTagName("link")
			for link in links:
				if link.attributes['rel'].value == 'alternate':
					PostRecord.PublishedURL = link.attributes['href'].value
				if link.attributes['rel'].value == 'edit':
					PostRecord.EditURL = link.attributes['href'].value
				if link.attributes['rel'].value == 'self':
					PostRecord.URL = link.attributes['href'].value
#			PostRecord.name = re.findall('http://(\w*).blogspot',PostRecord.URL)[0]
			self.Posts[PostRecord.id] = PostRecord

		dom.unlink()

	#
	# Function:		fetchBlogDetails
	# Description:
	#  Return a list of blogs
	#
	def fetchBlogDetails( self ):
		url = 'http://www.blogger.com/feeds/default/blogs'

		if self.authtoken:
			headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2'
			}
		else:
			headers = None

		response, content = self.http.request(url, 'GET', headers=headers)
		if response['status'] == '404':
			raise TGBError(content)
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TGBError(content)

		# --- Parse
		class Record:
			pass

		try:
			dom = minidom.parseString( content )
		except:
			print content
			raise
		feedNode = dom.getElementsByTagName("feed")[0]
		entryNodes = feedNode.getElementsByTagName("entry")

		self.Blogs = dict()
		for blog in entryNodes:
			BlogRecord = Record()
			BlogRecord.title = self.XMLText(blog.getElementsByTagName("title")[0])
			links = blog.getElementsByTagName("link")
			for link in links:
				if link.attributes['rel'].value == 'alternate':
					BlogRecord.URL = link.attributes['href'].value
				if link.attributes['rel'].value == 'http://schemas.google.com/g/2005#post':
					BlogRecord.PostURL = link.attributes['href'].value
#			BlogRecord.id = re.findall('http://www.blogger.com/feeds/()/posts',BlogRecord.PostURL)[0]
			BlogRecord.name = re.findall('http://(\w*).blogspot',BlogRecord.URL)[0]
			self.Blogs[BlogRecord.name] = BlogRecord

		dom.unlink()

	#
	# Function:		modifyPost
	# Description:
	#
	def modifyPost( self, atom, meta, entryID ):
		headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2'
			}

		# split entryID into PostID and BlogID
		(blogID, postID) = re.findall('^tag:blogger.com,1999:blog-(\d+)\.post-(\d+)$', entryID )[0]
		postURL = "http://www.blogger.com/feeds/%s/posts/default/%s" % \
			(blogID, postID)

		response, content = self.http.request( postURL, 'GET',
			headers=headers )

		# Check for a redirect
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TUPError(content)

		if response['status'] != '200':
			raise TUPError(content)

		# Modify XML to hold new article
		print "gitblogger: Received %d bytes of article ready to modify" % (len(content))

		try:
			dom = minidom.parseString( content )
		except:
			print content
			raise
		entryNode = dom.getElementsByTagName("entry")[0]
		contentNode = entryNode.getElementsByTagName("content")[0]

		newContent = dom.createElement('content')
		newContent.setAttribute('type', 'xhtml')
		newContent.appendChild( dom.createTextNode( atom ) )

		entryNode.replaceChild( newContent, contentNode )

		upload = dom.toxml()
		dom.unlink()

		print "gitblogger: Created replacement article, %d bytes" % (len(upload))

		headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2',
			'Content-Type':'application/atom+xml',
			}
		response, content = self.http.request( postURL, 'PUT',
			headers=headers,body=upload )

		# Check for a redirect
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TGBError(content)

		if response['status'] != '200':
			raise TGBError(content)



	#
	# Function:		deletePost
	# Description:
	#
	def deletePost( self, entryID ):
		headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2'
			}

		# split entryID into PostID and BlogID
		(blogID, postID) = re.findall('^tag:blogger.com,1999:blog-(\d+)\.post-(\d+)$', entryID )[0]
		postURL = "http://www.blogger.com/feeds/%s/posts/default/%s" % \
			(blogID, postID)

		response, content = self.http.request( postURL, 'DELETE',
			headers=headers )

		# Check for a redirect
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TUPError(content)

		if response['status'] != '200':
			raise TUPError(content)

	#
	# Function:		createPost
	# Description:
	#
	def createPost( self, body, meta, targetblog ):
		if not self.authtoken:
			raise TGBError("Not logged in while attempting upload")
		if not targetblog:
			raise TGBError("You must supply a blog name to upload to")

		if not targetblog in self.Blogs:
			raise TGBError("Target blog name \"%s\" not found in blogger.com list" % targetblog)

		blog = self.Blogs[targetblog]

		fsize = len(body)

		if self.options.verbose:
			print >> sys.stderr,  "----- Transmitting to blog"

		print >> sys.stderr,  "Uploading entry \"%s\", size %d" % (meta.title, fsize)

		headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2',
			'Content-Type':'application/atom+xml',
			'Content-Length':'%s' % fsize }

		response, content = self.http.request( blog.PostURL, 'POST',
			headers=headers,body=body )

		# Check for a redirect
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TGBError(content)

		if self.options.verbose:
			print >> sys.stderr,  "RX:", response

		if response['status'] != '201':
			raise TGBError(content)

		# Find the post ID
		try:
			dom = minidom.parseString( content )
		except:
			print content
			raise
		entryNode = dom.getElementsByTagName("entry")[0]
		id = self.XMLText(entryNode.getElementsByTagName("id")[0])
		dom.unlink()

		return id

	#
	# Function:		ikiwikiToAtom
	# Description:
	#
	def ikiwikiToAtom( self, rawsource ):
		ikiwiki = unicode(rawsource, "utf-8")

		# Extract all the ikiwiki directives
		directives = re.findall(u'\[\[!(.*)\]\]\n', ikiwiki )
		mdwn = re.sub(u'\[\[!(.*)\]\]\n','', ikiwiki ).strip()

		# Convert from mardown syntax to HTML
		html = markdown.markdown(mdwn)

		# Extract meta data from ikiwiki directives
		meta = Record()
		meta.title = None
		meta.date = None
		meta.categories = []
		for directive in directives:
			x = re.findall('meta title="(.*)"', directive)
			if len(x) > 0:
				meta.title = x[0];
			x = re.findall('meta date="(.*)"', directive)
			if len(x) > 0:
				meta.date = x[0];
			x = re.findall('tag (.*)', directive)
			if len(x) > 0:
				meta.categories.extend(x[0].split(' '))

		# Convert date to atom format
		if meta.date is not None:
			try:
				x = datetime.datetime.strptime(meta.date,"%Y-%m-%d %H:%M:%S")
			except ValueError:
				try:
					x = datetime.datetime.strptime(meta.date,"%Y-%m-%d %H:%M")
				except ValueError:
					x = datetime.datetime.strptime(meta.date,"%Y-%m-%d")
			meta.date = '<published>' + x.strftime('%Y-%m-%dT%H:%M:%S%z') + '</published>'

		# --- Add atom wrapper
		extras = "<category scheme='http://schemas.google.com/g/2005#kind' term='http://schemas.google.com/blogger/2008/kind#post'/>\n"
		# Tags
		for tag in meta.categories:
			extras = extras + "<category scheme='http://www.blogger.com/atom/ns#' term='%s' />\n" % (tag)

		# Draft mode
		if self.options.draft:
			extras = extras + """<app:control xmlns:app='http://www.w3.org/2007/app'>
  <app:draft>yes</app:draft>
</app:control>
"""

		atom = """<entry xmlns='http://www.w3.org/2005/Atom'>
  <title type='text'>%s</title>
  %s
<content type='xhtml'>
<div xmlns="http://www.w3.org/1999/xhtml">
%s
</div>
</content>
%s
</entry>
""" % (meta.title,meta.date,html,extras)

#		print >> sys.stderr,  atom

		return (atom,meta)


	#
	# Function:		authenticate
	# Description:
	#  Generate an authentication token to use for subsequent requests
	#
	def authenticate( self, login = None, password = None ):
		authtoken = None

		# Don't try to authenticate when no details supplied
		if not login or not password:
			return authtoken;

		# Create the authentication request
		auth_url = 'https://www.google.com/accounts/ClientLogin'
		auth_headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		auth_request = "Email=%s&Passwd=%s&service=blogger&accountType=GOOGLE" % \
			(login, password)

		if self.options.verbose:
			print >> sys.stderr,  "---- Authenticating"

		# Make the request
		response, content = self.http.request(auth_url, 'POST',
			body=auth_request, headers=auth_headers)

		if self.options.verbose:
			print >> sys.stderr,  "BLOGGER>",content
			print >> sys.stderr,  "BLOGGER>",response

		if response['status'] == '200':
			authtoken = re.search('Auth=(\S*)', content).group(1).strip()

		return authtoken


	#
	# Function:		readConfigFile
	# Description:
	#  Read default settings from git config file
	#
	def readConfigFile( self ):
		# --------------
		# [gitblogger]
		#   username = somename@gmail.com
		#   password = secret_gmail_password
		#   notesref = notes/gitblogger
		# 
		# [blog "blogName"]
		#   blogbranch = master
		#   repositorypath = blog/
		#   sendasdraft = false
		# --------------

		self.options.username = subprocess.Popen(["git", "config", "--get", "gitblogger.username"], stdout=subprocess.PIPE).communicate()[0].strip()
		self.options.password = subprocess.Popen(["git", "config", "--get", "gitblogger.password"], stdout=subprocess.PIPE).communicate()[0].strip()
		self.notesref = subprocess.Popen(["git", "config", "--get", "gitblogger.notesref"], stdout=subprocess.PIPE).communicate()[0].strip()

		lines = subprocess.Popen(["git", "config", "--get-regexp", "^blog\."], stdout=subprocess.PIPE).communicate()[0].split('\n')

		self.gitblogs = dict()

		for line in lines:
			linelist = line.split(' ',2)
			if len( linelist ) != 2:
				continue
			(key, value) = tuple( line.split(' ',2) )
			key = key.split('.')
			if key[0] != 'blog':
				continue
			if len(key) != 3:
				continue
			if not self.gitblogs.has_key(key[1]):
				self.gitblogs[key[1]] = dict()
			self.gitblogs[key[1]][key[2]] = value


	#
	# Function:		readCommandLine
	# Description:
	#  Parse the command line with OptionParser; which supplies all the
	#  niceties for us (like --help, --version and validating the inputs)
	#
	def readCommandLine( self ):
		# Configure parser
		parser = OptionParser(
			usage="usage: %prog [options]",
			version="%prog 1.0")
		# "-h", "--help" supplied automatically by OptionParser
		parser.add_option( "-v", "--verbose", dest="verbose",
			action="store_true",
			help="show verbose output")
		parser.add_option( "-d", "--draft", dest="draft",
			action="store_true", default=self.options.draft,
			help="upload as draft articles")
		parser.add_option( "-l", "--login", dest="username",
			metavar="USERNAME", type='string', default=self.options.username,
			help="the username of your google account [default:%default]")
		parser.add_option( "-p", "--password", dest="password",
			metavar="PASSWORD", type='string', default=self.options.password,
			help="the password of your google account [default:NOT SHOWN]")
#		parser.add_option( "-r", "--resize", dest="targetsize",
#			metavar="WIDTH", type='int', default=self.options.targetsize,
#			help="the width the image should be in picasa [default:%default]")
#		parser.add_option( "", "--list", dest="mode",
#			action="store_const", const="list",
#			help="list blogs")
#		parser.add_option( "", "--listposts", dest="mode",
#			action="store_const", const="listposts",
#			help="list posts")
#		parser.add_option( "", "--deleteall", dest="mode",
#			action="store_const", const="deleteall",
#			help="delete all posts")
		parser.add_option( "", "--preview", dest="preview",
			action="store_const", const="preview",
			help="preview Atom post")
		parser.set_defaults(mode=self.options.mode, preview=False)

		# Run the parser
		(self.options, args) = parser.parse_args( self.argv[1:] )

		# Copy the positional arguments into self
		self.positionalparameters = args

		if len(self.positionalparameters) == 3:
			self.options.mode = "commandline"

	#
	# Function:		XMLText
	# Description:
	#
	def XMLText( self, xmlnode ):
		ret = ""
		for node in xmlnode.childNodes:
			if node.nodeType == node.TEXT_NODE:
				ret = ret + node.data
		return ret

	#
	# Function:		__str__
	# Description:
	#  Dump the contents of this class to a string
	#
	def __str__( self ) :
		s = repr(self) + "\n";
		for var in self.__dict__ :
			s = s + " - " + var + " = " + str(self.__dict__[var]) + "\n"
		return s


# ----- Main
#
# Function:		main
# Description:
#
def main( argv = None ):
	# Default arguments from command line
	if argv is None:
		argv = sys.argv

	# Locale
	locale.setlocale( locale.LC_ALL, '' );

	app = TGitBlogger( argv )

	# --- Begin
	try:
		app.run()

	# Simply display TGBErrors
	except TGBError, e:
		print >> sys.stderr,  "gitblogger: ERROR:",e.args[0]


# ----- Module check
#
# __name__ is set to "__main__" when this is the top module
# if this module is loaded because of an "import" then this
# won't get run -- perfect
if __name__ == "__main__":
	sys.exit( main() )

