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
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Notes
#   http://code.google.com/apis/blogger/docs/2.0/developers_guide_protocol.html
#
# ----------------------------------------------------------------------------

# ----- Includes

# Standard library
import sys
import os
import subprocess
import locale
import time
import re
import codecs
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

		if len(self.options.username) == 0 or \
			len(self.options.password) == 0:
			print >> sys.stderr, "gitblogger: No gitblogger settings found in git config, aborting"
			return

		if self.options.verbose:
			print >> sys.stderr,  " --- Verbose mode active"
			print >> sys.stderr,  self
			httplib2.debuglevel = 20

		# Create a httplib object for doing the web work
		self.http = httplib2.Http()

		if self.options.mode == 'post-receive':
			print >> sys.stderr, "gitblogger: Running in git post-receive hook mode; reading changes from stdin..."
			while True:
				line = sys.stdin.readline().strip()
				if not line:
					break
				line = line.split(' ')
				if len(line) < 3:
					continue
				(oldrev, newrev, refname) = tuple(line)
				print >> sys.stderr, "gitblogger: %s %s -> %s" % (refname, oldrev, newrev)

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

		elif self.options.mode == 'listblogs':
			# Establish authentication token
			print >> sys.stderr, "gitblogger: Logging into Google GData API as", self.options.username
			self.authtoken = self.authenticate( self.options.username, self.options.password )
			if self.authtoken is None:
				raise TGBError("GData authentication failed")
			print >> sys.stderr, "gitblogger: Success, authtoken is",self.authtoken

			print >> sys.stderr, "gitblogger: Fetching details of blogs owned by", self.options.username
			self.fetchBlogDetails()

			for blog in self.Blogs.itervalues():
				print blog.name,blog.id

		elif self.options.mode == 'sync':
			self.synchroniseTrackingIDs()

		elif self.options.mode == 'testmd':
			for filename in self.positionalparameters:
				print "---",filename
				f = codecs.open(filename, mode='rb', encoding='utf-8')
				ikiwiki = f.read()
				(mdwn, meta) = self.ikiwikiToMarkdown( ikiwiki )
				print repr(meta.__dict__)
				print mdwn.encode('utf-8')

		elif self.options.mode == 'bootstrap':
			# Establish authentication token
			print >> sys.stderr, "gitblogger: Logging into Google GData API as", self.options.username
			self.authtoken = self.authenticate( self.options.username, self.options.password )
			if self.authtoken is None:
				raise TGBError("GData authentication failed")
			print >> sys.stderr, "gitblogger: Success, authtoken is",self.authtoken

			print >> sys.stderr, "gitblogger: Fetching details of blogs owned by", self.options.username
			self.fetchBlogDetails()

			for blog in self.positionalparameters:
				print >> sys.stderr, "---",blog
				self.generateImportXML( blog )
			print >> sys.stderr, "gitblogger: After uploading this XML to blogger, you",
			print >> sys.stderr, "should run --sync mode to download the tracking IDs"

	#
	# Function:		generateImportXML
	# Description:
	#
	def generateImportXML( self, blogname ):

		if not self.gitblogs.has_key( blogname ):
			print >> sys.stderr, "gitblogger: Skipping unknown blog", blogname
			return

		print >> sys.stderr, "gitblogger: Generating importable XML for blog,", blogname
		blog = self.gitblogs[blogname]

		print >> sys.stderr, "gitblogger: Looking up local post titles in repository directory",os.path.normpath(blog['repositorypath']) + os.sep
		repoarticles = subprocess.Popen(["git", "ls-tree", "--full-tree", \
				blog['blogbranch'], \
				os.path.normpath(blog['repositorypath']) + os.sep ], \
				stdout=subprocess.PIPE).communicate()[0].strip()
		repoarticles = repoarticles.split('\n')

		BloggerBlog = self.Blogs[blogname]

		print """<?xml version='1.0' encoding='UTF-8'?>
<?xml-stylesheet href="http://www.blogger.com/styles/atom.css" type="text/css"?>
<feed xmlns='http://www.w3.org/2005/Atom' xmlns:openSearch='http://a9.com/-/spec/opensearchrss/1.0/' xmlns:georss='http://www.georss.org/georss' xmlns:gd='http://schemas.google.com/g/2005' xmlns:thr='http://purl.org/syndication/thread/1.0'>
<id>%s</id>
<link rel='http://schemas.google.com/g/2005#feed' type='application/atom+xml' href='%s'/>
<link rel='self' type='application/atom+xml' href='%s'/>
<link rel='http://schemas.google.com/g/2005#post' type='application/atom+xml' href='%s'/>
<link rel='alternate' type='text/html' href='%s'/>
<generator version='7.00' uri='http://www.blogger.com'>Blogger</generator>""" % \
			(BloggerBlog.id, BloggerBlog.FeedURL, BloggerBlog.SelfURL, BloggerBlog.PostURL, BloggerBlog.URL )

		print >> sys.stderr, "gitblogger: Extracting titles from '[[!meta title]]' directives",
		LocalObject = dict()
		for treerecord in repoarticles:
			treerecord = treerecord.split(' ')
			article = treerecord[2].split('\t')
			if not article[1].endswith('.mdwn'):
				continue
			md_source = subprocess.Popen(["git", "cat-file", "-p", \
					article[0]], stdout=subprocess.PIPE).communicate()[0]
			md_source = unicode( md_source, 'utf-8' )
			(mdwn, meta) = self.ikiwikiToAtom( md_source, 'Does it matter what goes here' )

			if meta.title is None:
				print >> sys.stderr, ""
				print >> sys.stderr, "gitblogger: No title found in",article[1]
				print >> sys.stderr, ""
				continue

			print mdwn

			LocalObject[meta.title] = article[0]
			sys.stderr.write('.')

		print >> sys.stderr, ""
		print >> sys.stderr, "gitblogger: %d titles extracted from repository-stored articles" % ( len(LocalObject) )

		# Close opening tag
		print "</feed>"


	#
	# Function:		synchroniseTrackingIDs
	# Description:
	#
	def synchroniseTrackingIDs( self ):
		if len(self.positionalparameters) == 0:
			raise TGBError("No blog names supplied on command line")

		# Establish authentication token
		print >> sys.stderr, "gitblogger: Logging into Google GData API as", self.options.username
		self.authtoken = self.authenticate( self.options.username, self.options.password )
		if self.authtoken is None:
			raise TGBError("GData authentication failed")
		print >> sys.stderr, "gitblogger: Success, authtoken is",self.authtoken

		print >> sys.stderr, "gitblogger: Fetching details of blogs owned by", self.options.username
		self.fetchBlogDetails()

		for blogname in self.positionalparameters:
			if not self.gitblogs.has_key( blogname ):
				print >> sys.stderr, "gitblogger: Skipping unknown blog", blogname
				continue
			print >> sys.stderr, "gitblogger: Syncing tracking IDs for blog,", blogname
			blog = self.gitblogs[blogname]

			print >> sys.stderr, "gitblogger: Looking up local post titles in repository directory",os.path.normpath(blog['repositorypath']) + os.sep
			repoarticles = subprocess.Popen(["git", "ls-tree", "--full-tree", \
					blog['blogbranch'], \
					os.path.normpath(blog['repositorypath']) + os.sep ], \
					stdout=subprocess.PIPE).communicate()[0].strip()
			repoarticles = repoarticles.split('\n')

			print >> sys.stderr, "gitblogger: Extracting titles from '[[!meta title]]' directives",
			LocalObject = dict()
			for treerecord in repoarticles:
				treerecord = treerecord.split(' ')
				article = treerecord[2].split('\t')
				if not article[1].endswith('.mdwn'):
					continue
				md_source = subprocess.Popen(["git", "cat-file", "-p", \
						article[0]], stdout=subprocess.PIPE).communicate()[0]
				md_source = unicode( md_source, 'utf-8' )
				(mdwn, meta) = self.ikiwikiToMarkdown( md_source )

				if meta.title is None:
					print >> sys.stderr
					print >> sys.stderr, "gitblogger: No title found in",article[1]
					print >> sys.stderr
					continue

				LocalObject[meta.title] = article[0]
				sys.stderr.write('.')

			print >> sys.stderr
			print >> sys.stderr, "gitblogger: %d titles extracted from repository-stored articles" % ( len(LocalObject) )

			print >> sys.stderr, "gitblogger: Fetching post details for", blogname
			self.fetchPostDetails( blogname )
			print >> sys.stderr, "gitblogger: Found %d remote blog posts" % ( len(self.Posts) )
			notecount = 0
			for post in self.Posts.itervalues():
				if not LocalObject.has_key(post.title):
					continue
				retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
					"add", "-f", "-m", post.id, LocalObject[post.title]], stdout=subprocess.PIPE)
				if retcode != 0:
					print >> sys.stderr, "gitblogger: Failed to record tracking ID in git repository"
				notecount = notecount + 1
			print >> sys.stderr, "gitblogger: Sync complete, %d tracking IDs written or rewritten" % (notecount)



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
		print >> sys.stderr, "gitblogger: Logging into Google GData API as", self.options.username
		self.authtoken = self.authenticate( self.options.username, self.options.password )
		if self.authtoken is None:
			raise TGBError("GData authentication failed")
		print >> sys.stderr, "gitblogger: Success, authtoken is",self.authtoken

		print >> sys.stderr, "gitblogger: Fetching details of blogs owned by", self.options.username
		self.fetchBlogDetails()

		for change in difftree:
			change = change.split(' ', 5)
			status = change[4].split('\t')

			fromhash = change[2]
			tohash = change[3]

			print >> sys.stderr, "gitblogger: ---",status[1]
			while True: 
				if status[0][0] == 'A':
					print >> sys.stderr, "gitblogger: Fetching new article from repository,",status[1]
					md_source = subprocess.Popen(["git", "cat-file", "-p", \
						tohash], stdout=subprocess.PIPE).communicate()[0]
					md_source = unicode( md_source, 'utf-8' )
					print >> sys.stderr, "gitblogger: Converting %d byte article to XHTML" % (len(md_source))
					# Install plugin handler for different file types
					# here.  At the moment this is hard coded for
					# ikiwiki-style markdown
					try:
						(atom, meta) = self.ikiwikiToAtom(md_source)
					except Exception, e:
						raise TGBError("Couldn't convert article to XHTML: %s" % (e.args[0]) )
					print >> sys.stderr, "gitblogger: Converted article, \"%s\", is %d bytes, uploading..." % (meta.title, len(atom))
					if self.options.preview:
						print atom
						break
					id = self.createPost( atom, meta, blogname )
					print >> sys.stderr, "gitblogger: Upload complete, article was assigned the id, \"%s\"" % (id)
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"add", "-f", "-m", id, tohash], stdout=subprocess.PIPE)
					if retcode != 0:
						print >> sys.stderr, "gitblogger: Failed to record tracking ID in git repository"

				elif status[0][0] == 'C':
					print >> sys.stderr, "gitblogger: Article copied %s -> %s" % (status[1], status[2])

				elif status[0][0] == 'D':
					print >> sys.stderr, "gitblogger: Article deleted", status[1]
					print >> sys.stderr, "gitblogger: Looking up corresponding blog post tracking ID"
					gitproc = subprocess.Popen(["git", "notes", "--ref", self.notesref, \
						"show", fromhash], stdout=subprocess.PIPE)
					postid = gitproc.communicate()[0].strip()
					retcode = gitproc.wait()
					if retcode != 0:
						print >> sys.stderr, "gitblogger: Lookup failed, can't delete remote blog article without a tracking ID"
						break
					print >> sys.stderr, "gitblogger: Removing remote posting with tracking ID,",postid
					self.deletePost( postid )
					print >> sys.stderr, "gitblogger: Removing local copy of tracking ID"
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"remove", fromhash], stdout=subprocess.PIPE)

				elif status[0][0] == 'M':
					print >> sys.stderr, "gitblogger: Article modified", status[1]
					print >> sys.stderr, "gitblogger: Copying tracking ID from %s -> %s" % (fromhash, tohash)
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"copy", "-f", fromhash, tohash], \
						stdout=subprocess.PIPE)
					if retcode != 0:
						print >> sys.stderr, "gitblogger: Couldn't copy tracking ID, treating article modification as article creation"
						status[0] = 'A'
						continue
					print >> sys.stderr, "gitblogger: Fetching replacement article from repository,",status[1]
					md_source = subprocess.Popen(["git", "cat-file", "-p", \
						tohash], stdout=subprocess.PIPE).communicate()[0]
					md_source = unicode( md_source, 'utf-8' )
					print >> sys.stderr, "gitblogger: Converting %d byte article to XHTML" % (len(md_source))
					# Install plugin handler for different file types
					# here.  At the moment this is hard coded for
					# ikiwiki-style markdown
					try:
						(mdwn, meta) = self.ikiwikiToMarkdown(md_source)
					except Exception, e:
						raise TGBError("Couldn't convert article to XHTML: %s" % (e.args[0]) )
					print >> sys.stderr, "gitblogger: Converted article, \"%s\", is %d bytes, uploading..." % (meta.title, len(mdwn))
					if self.options.preview:
						print mdwn
						break
					# Fetch postid
					gitproc = subprocess.Popen(["git", "notes", "--ref", self.notesref, \
						"show", tohash], stdout=subprocess.PIPE)
					postid = gitproc.communicate()[0].strip()
					retcode = gitproc.wait()
					if retcode != 0:
						print >> sys.stderr, "gitblogger: Lookup failed, can't delete remote blog article without a tracking ID"
						break
					print >> sys.stderr, "gitblogger: Modifying remote post,", postid
					self.modifyPost( mdwn, meta, postid )
					print >> sys.stderr, "gitblogger: Post modified"

				elif status[0][0] == 'R':
					print >> sys.stderr, "gitblogger: Article renamed %s -> %s" % (status[1], status[2])
					print >> sys.stderr, "gitblogger: Updating local post ID tracking information"
					retcode = subprocess.call(["git", "notes", "--ref", self.notesref, \
						"copy", "-f", fromhash, tohash], \
						stdout=subprocess.PIPE)
					if retcode != 0:
						print >> sys.stderr, "gitblogger: Couldn't copy tracking ID, treating article modification as article creation"
						status = ['A', status[2]]
						continue
					if status[0][1:] != '100':
						print >> sys.stderr, "gitblogger: Converting rename with change to a modify for",status[1]
						status = ['M', status[2]]
						continue

				elif status[0][0] == 'T':
					print >> sys.stderr, "gitblogger: Ignoring change of file type for", status[1]

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
			BlogRecord.id = self.XMLText(blog.getElementsByTagName("id")[0])
			links = blog.getElementsByTagName("link")
			for link in links:
				if link.attributes['rel'].value == 'self':
					BlogRecord.SelfURL = link.attributes['href'].value
				if link.attributes['rel'].value == 'alternate':
					BlogRecord.URL = link.attributes['href'].value
				if link.attributes['rel'].value == 'http://schemas.google.com/g/2005#feed':
					BlogRecord.FeedURL = link.attributes['href'].value
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
	def modifyPost( self, mdwn, meta, entryID ):
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
		print >> sys.stderr, "gitblogger: Received %d bytes of article ready to modify" % (len(content))

		try:
			dom = minidom.parseString( content )
		except:
			print content
			raise
		entryNode = dom.getElementsByTagName("entry")[0]
		contentNode = entryNode.getElementsByTagName("content")[0]

		# Make a new XML tree from the replacement article, we have to
		# supply a root node as XML requires that the whole document be
		# wrapped in something; it actually doesn't matter what as we're
		# going to strip the container off anyway
		x = u"<content>" + markdown.markdown(mdwn) + u"</content>"
		tempParsingDom = minidom.parseString( x.encode('utf-8') )

		# Import the new content into the existing article's DOM,
		# preserving the XML tree.
		newArticle = dom.importNode( tempParsingDom.childNodes[0], True )
		# Import finished, we can do away with the temporary DOM
		tempParsingDom.unlink()

		# The new article is now attached to the old DOM.  We still have
		# to put it somewhere in the tree, we do that by making a new
		# container element for the content and swapping the new element
		# for the old element
		newContent = dom.createElement('content')
		newContent.setAttribute('type', 'xhtml')
		# attach the article to the new content node
		newContent.appendChild( newArticle )
		# Replace the old content node with the new content node
		entryNode.replaceChild( newContent, contentNode )

		categoryNodes = entryNode.getElementsByTagName('catgegory')
		for categoryNode in categoryNodes:
			if categoryNode.getAttribute( 'scheme' ) == "http://www.blogger.com/atom/ns#":
				entryNode.removeChild( categoryNode )
		for tag in meta.categories:
			categoryNode = dom.createElement('category')
			categoryNode.setAttribute( 'scheme', "http://www.blogger.com/atom/ns#" )
			categoryNode.setAttribute( 'term', tag )
			entryNode.appendChild( categoryNode )

		titleNode = entryNode.getElementsByTagName('title')[0]
		newTitle = dom.createTextNode( meta.title )
		titleNode.replaceChild( newTitle, titleNode.firstChild )

		if meta.date is not None:
			publishedNode = entryNode.getElementsByTagName('published')[0]
			if publishedNode is not None:
				newPublished = dom.createTextNode( meta.date )
				publishedNode.replaceChild( newPublished, publishedNode.firstChild )

		upload = dom.toxml()
		dom.unlink()

		print >> sys.stderr, "gitblogger: Created replacement article, %d bytes" % (len(upload))

		headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2',
			'Content-Type':'application/atom+xml',
			}
		response, content = self.http.request( postURL, 'PUT',
			headers=headers,body=upload.encode('utf-8') )

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

		print >> sys.stderr, "gitblogger: Uploading entry \"%s\", size %d" % (meta.title, fsize)

		headers = { 'Authorization': 'GoogleLogin auth=%s' % self.authtoken,
			'GData-Version':'2',
			'Content-Type':'application/atom+xml',
			'Content-Length':'%s' % fsize }

		response, content = self.http.request( blog.PostURL, 'POST',
			headers=headers, body=body )

		# Check for a redirect
		while response['status'] == '302':
			response, content = self.http.request(response['location'], 'GET')
			if response['status'] == '404':
				raise TGBError(content)


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
	# Function:		ikiwikiToMarkdown
	# Description:
	#
	def ikiwikiToMarkdown( self, ikiwiki ):

		# Extract all the ikiwiki directives
		pattern = re.compile(r'\[\[!(.*?)\]\]', re.DOTALL )
		directives = pattern.findall(ikiwiki)
		mdwn = pattern.sub('', ikiwiki).strip()

		# Workaround bug in python markdown module
		# http://www.freewisdom.org/projects/python-markdown/Tickets/000059
		mdwn = re.sub(r'\[(.+?)\]: *<(.+)>', r'[\1]: \2', mdwn )

		# Extract meta data from ikiwiki directives
		meta = Record()
		meta.title = None
		meta.date = None
		meta.categories = []
		for directive in directives:
			directive = directive.replace("\n",' ')
			x = re.findall('meta title="(.*)"', directive)
			if len(x) > 0:
				meta.title = x[0];
			x = re.findall('meta date="(.*)"', directive)
			if len(x) > 0:
				meta.date = x[0];
			x = re.findall('tag (.*)', directive)
			if len(x) > 0:
				meta.categories.extend(x[0].split(' '))

		if meta.date is not None:
			try:
				localtime_tuple = time.strptime( meta.date, "%Y-%m-%d %H:%M:%S")
			except ValueError:
				try:
					localtime_tuple = time.strptime( meta.date, "%Y-%m-%d %H:%M")
				except ValueError:
					localtime_tuple = time.strptime( meta.date, "%Y-%m-%d")
			# Now we have a structure in local time, we convert to epoch
			# time
			absolute_epoch = time.mktime(localtime_tuple)
			# Convert to UTC
			utc_tuple = time.gmtime(absolute_epoch)
			# Instruct blogger that this is a UTC time
			meta.date = time.strftime('%Y-%m-%dT%H:%M:%S.000+00:00', utc_tuple )

		return (mdwn, meta)

	#
	# Function:		ikiwikiToAtom
	# Description:
	#
	def ikiwikiToAtom( self, rawsource, exportpostid = None ):
		(mdwn, meta) = self.ikiwikiToMarkdown( rawsource )

		# Convert from markdown syntax to HTML
		html = markdown.markdown(mdwn)

		# Convert date to atom format
		atomdate = None
		if meta.date is not None:
			atomdate = '<published>' + meta.date + '</published>'

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

		if exportpostid is None:
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
""" % (meta.title, atomdate, html, extras)
		else:
			extras = extras + "<thr:total>0</thr:total>"
			atom = """<entry xmlns='http://www.w3.org/2005/Atom'>
  <id>%s</id>
  <title type='text'>%s</title>
  %s
<content type='xhtml'>
<div xmlns="http://www.w3.org/1999/xhtml">
%s
</div>
</content>
%s
</entry>
""" % (exportpostid, meta.title, atomdate, html, extras)

		return (atom.encode('utf-8'),meta)


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

		if len(self.notesref) == 0:
			self.notesref = 'notes/gitblogger'

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
				# Defaults
				self.gitblogs[key[1]]['blogbranch'] = 'master'
				self.gitblogs[key[1]]['repositorypath'] = ''
				self.gitblogs[key[1]]['sendasdraft'] = False

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
		parser.add_option( "", "--preview", dest="preview",
			action="store_const", const="preview",
			help="preview Atom post")
		parser.add_option( "", "--listblogs", dest="mode",
			action="store_const", const="listblogs",
			help="List the available blogs")
		parser.add_option( "", "--testmd", dest="mode",
			action="store_const", const="testmd",
			help="Test the ikiwiki-to-markdown engine from the supplied filenames")
		parser.add_option( "", "--sync", dest="mode",
			action="store_const", const="sync",
			help="Use article titles to look up post IDs")
		parser.add_option( "", "--bootstrap", dest="mode",
			action="store_const", const="bootstrap",
			help="Generate a blogger-compatible XML file to mass upload a blog")
		parser.set_defaults(mode=self.options.mode, preview=False)

		# Run the parser
		(self.options, args) = parser.parse_args( self.argv[1:] )

		# Copy the positional arguments into self
		self.positionalparameters = args

		if self.options.mode == 'post-receive' and len(self.positionalparameters) == 3:
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

