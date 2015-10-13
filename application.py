# -*- coding: utf-8 -*-
__author__ = 'Shtav'
from flask import Flask, render_template, request, jsonify, url_for, flash, make_response, abort
from urlparse import urljoin
from werkzeug.contrib.atom import AtomFeed
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, exc
from database_setup import Base, Category, Item, User
from flask import session as login_session
import random
import string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
import re
import requests
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer


app = Flask(__name__)

APPLICATION_NAME = "Catalog"
CLIENT_ID = json.loads(open('client_secrets.json', 'r').read())['web']['client_id']

# Connect to Database and create database session
engine = create_engine('sqlite:///catalog.db')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()

default_img_url = 'http://img2.wikia.nocookie.net/__cb20130511180903/legendmarielu/images/b/b4/No_image_available.jpg'
default_picture_url = 'https://encrypted-tbn2.gstatic.com/images?q=tbn:ANd9GcRm4xo-buhgKdRnRVIwPQhCC5SiF4hBn4VJOIP3k2gFy4CnfJYOeSsh'

email_pattern = "^(([-a-zA–Z0-9!#$%&'*+/=?^_`{|}~.])|((^|\.)\"((\\\")|(\\\\)|\w|[-!#$%&'*+/=?^_`{|}~. \[\])(,:;<>@])*\"\.?)*)+@[-a-zA-Z0-9.]+\.[A-Za-z]{1,3}"

##########################
# GET: RSS endpoint
##########################
@app.route('/api/recent.atom')
def recentFeed():
	feed = AtomFeed('Recent Articles',
	                feed_url=request.url, url=request.url_root)
	try:
		items = session.query(Item).order_by(Item.timestamp).limit(15).all()
	except exc.NoResultFound:
		response = make_response(
			json.dumps('No items found. Please create some. %s' % url_for('/'),
			           400))
		response.headers['Content-Type'] = 'application/json'
		return response
	for item in items:
		creator = session.query(User).filter_by(id=item.user_id).one()
		feed.add(item.name, unicode(item.description),
		         content_type='html',
		         author=creator.name,
		         updated=item.timestamp,
		         url=make_external('category/' + str(item.category_id) +
		                           '/item/' + str(item.id) + '/'))
	return feed.get_response()

def make_external(url):
	return urljoin(request.url_root, url)

##########################
# GET: API and Page Rendering
##########################
# send initial page, client-side will handle routing
@app.route('/')
@app.route('/<path:path>')
def index(path=''):
	if 'username' not in login_session:
		return render_template('index.html')
	else:
		print(login_session['username'])
		return render_template('index.html', logged='true')

# render login-form.html with state and client_id
@app.route('/loginform')
def loginForm():
	state = generateRandomString()
	login_session['state'] = state
	login_session['_csrf_token'] = state
	return render_template("login-form.html", client_id=CLIENT_ID)

# CRUD operations for categories (complete)
# TODO: accept args more queries
@app.route('/api/category/', methods=['GET', 'POST'])
@app.route('/api/category/<int:id>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def categoryAPI(id=''):
	# return categories
	if request.method == 'GET' and 'user_id' in request.args:
		categories = session.query(Category).filter_by(user_id=request.args['user_id']).order_by(Category.timestamp).all()
		return json.dumps([r.serialize for r in categories])

	if request.method == 'GET' and id == '':
		categories = session.query(Category).order_by(Category.timestamp).all()
		return json.dumps([r.serialize for r in categories])
	elif request.method == 'GET':
		category = session.query(Category).filter_by(id=id).one()
		return json.dumps(category.serialize)

	if 'username' in login_session:
		# CREATE a CATEGORY, requires login
		if request.method == 'POST':
			if 'img_url' not in request.json:
				img_url = ''
			else:
				img_url = request.json['img_url']
			if 'name' in request.json and validateExists(request.json['name']):
				valid, img_url, msg = validateImageUrl(img_url)
				# Save new category.
				if valid == 200:
					newCategory = Category(name=request.json['name'], img_url=img_url, user_id=login_session['user_id'])
					session.add(newCategory)
					session.commit()
					return json.dumps({'message':'Category created.', 'id': newCategory.id}), 201
				else:
					return jsonify(message=msg), valid
			else:
				return jsonify(message='You must enter a name.'), 422
		# END CREATE

		# get category for PUT or DELETE, user_id, must match that of category.user_id for PUT or DELETE
		category = session.query(Category).filter_by(id=id).one()
		if category is None:
			return jsonify(message='Category not found.'), 404
		if category.user_id != login_session['user_id']:
			jsonify(message='You are not the creator.'), 401

		# EDIT a CATEGORY, requires login and user_id match
		if request.method == 'PUT':
			if 'name' in request.json and validateExists(request.json['name']):
				if 'img_url' not in request.json:
					img_url = ''
				else:
					img_url = request.json['img_url']
				valid, img_url, msg = validateImageUrl(img_url)
				# Save edited category.
				if valid == 200:
					category.name = request.json['name']
					category.img_url = img_url
					session.add(category)
					session.commit()
					return json.dumps({'message':'Category updated.', 'id': category.id}), 202
				else:
					return jsonify(message=msg), valid
			else:
				return jsonify(message='You must enter a name.'), 422
		# END EDIT

		# DELETE a CATEGORY, requires category id and signed user_id to match category.user_id
		if request.method == 'DELETE':
			session.delete(category)
			session.commit()
			return jsonify(message='Category deleted.'), 204
		# END DELETE

	# Request that require login, but user is not loggedin
	else:
		abort(401)


# CRUD operations for items (complete)
# TODO: accept args for more types of queries
@app.route('/api/item/', methods=['GET', 'POST'])
@app.route('/api/item/<int:id>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def itemAPI(id=''):
	# return items
	if request.method == 'GET' and 'category_id' in request.args:
		items = session.query(Item).filter_by(category_id=request.args['category_id']).order_by(Item.timestamp).all()
		return json.dumps([r.serialize for r in items])
	if request.method == 'GET' and 'user_id' in request.args:
		items = session.query(Item).filter_by(user_id=request.args['user_id']).order_by(Item.timestamp).all()
		return json.dumps([r.serialize for r in items])

	if request.method == 'GET' and id == '':
		items = session.query(Item).order_by(Item.timestamp).all()
		return json.dumps([r.serialize for r in items])
	elif request.method == 'GET':
		item = session.query(Item).filter_by(id=id).one()
		return json.dumps(item.serialize)

	if 'username' in login_session:
		# verify that the user owns the category in which they are creating an item
		if request.method != 'DELETE':
			if 'category_id' not in request.json:
				return jsonify(message='You must select a category.'), 400
			category = session.query(Category).filter_by(id=request.json['category_id']).one()
			if category.user_id != login_session['user_id']:
				return jsonify(message='You do not own the category for which this item is to be created.'), 401

		# CREATE an ITEM, requires login
		if request.method == 'POST':
			if 'img_url' not in request.json:
				img_url = ''
			else:
				img_url = request.json['img_url']
			if 'name' in request.json and validateExists(request.json['name']) and 'category_id' in request.json and validateExists(request.json['category_id']):
				valid, img_url, msg = validateImageUrl(img_url)
				# Save new item.
				if valid == 200:
					if 'description' not in request.json:
						description = ''
					else:
						description = request.json['description']
					new_item = Item(name=request.json['name'], description=description,
					     img_url=img_url, category_id=request.json['category_id'], user_id=login_session['user_id'])
					session.add(new_item)
					session.commit()
					return json.dumps({'message': 'Item created.', 'category_id': new_item.category_id, 'id': new_item.id}), 201
				else:
					return jsonify(message=msg), valid
			else:
				return jsonify(message='You must enter a name.'), 422
		# END CREATE

		# get item for PUT or DELETE, user_id, must match that of category.user_id for PUT or DELETE
		item = session.query(Item).filter_by(id=id).one()
		if item is None:
			return jsonify(message='Item not found.'), 404
		if item.user_id != login_session['user_id']:
			jsonify(message='You are not the creator.'), 401
		# EDIT an ITEM, requires login and user_id match
		if request.method == 'PUT':
			if 'name' in request.json and validateExists(request.json['name']) and 'category_id' in request.json and validateExists(request.json['category_id']):
				if 'img_url' not in request.json:
					img_url = ''
				else:
					img_url = request.json['img_url']
				valid, img_url, msg = validateImageUrl(img_url)
				# Save edited item.
				if valid == 200:
					item.name = request.json['name']
					item.description = request.json['description']
					item.img_url = img_url
					item.category_id = request.json['category_id']
					session.add(item)
					session.commit()
					return json.dumps({'message':'Item updated.', 'category_id': item.category_id, 'id': item.id}), 202
				else:
					return jsonify(message=msg), valid
			else:
				return jsonify(message='You must enter a name.'), 422
		# END EDIT

		# DELETE an ITEM, requires item id and user_id match
		if request.method == 'DELETE':
			session.delete(item)
			session.commit()
			return jsonify(message='Category deleted.'), 204
		# END DELETE

	# Request that require login, but user is not loggedin
	else:
		abort(401)


##########################
# User Authentication
##########################
@app.route('/api/gconnect', methods=['POST'])
def gconnect():
	# Validate state token
	if request.args.get('state') != login_session['_csrf_token']:
		response = make_response(json.dumps('Invalid state parameter.'), 401)
		response.headers['Content-Type'] = 'application/json'
		return response
	# Obtain authorization code, now compatible with Python3
	code = request.data.decode('utf-8')

	try:
		# Upgrade the authorization code into a credentials object
		oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
		oauth_flow.redirect_uri = 'postmessage'
		credentials = oauth_flow.step2_exchange(code)
	except FlowExchangeError:
		# the gplus oauth call back is firing multiple times and before the first request can finish
		# a solution is being searched for
		response = make_response(json.dumps('Let this error fail silently.'), 200)
		response.headers['Content-Type'] = 'application/json'
		return response


	login_session['code'] = code
	# Check that the access token is valid.
	access_token = credentials.access_token
	url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
				 % access_token)
	# Submit request, parse response - Python3 compatible
	h = httplib2.Http()
	response = h.request(url, 'GET')[1]
	str_response = response.decode('utf-8')
	result = json.loads(str_response)

	# If there was an error in the access token info, abort.
	if result.get('error') is not None:
		response = make_response(json.dumps(result.get('error')), 500)
		response.headers['Content-Type'] = 'application/json'

	# Verify that the access token is used for the intended user.
	gplus_id = credentials.id_token['sub']
	if result['user_id'] != gplus_id:
		response = make_response(
			json.dumps("Token's user ID doesn't match given user ID."), 401)
		response.headers['Content-Type'] = 'application/json'
		return response

	# Verify that the access token is valid for this app.
	if result['issued_to'] != CLIENT_ID:
		response = make_response(
			json.dumps("Token's client ID does not match app's."), 401)
		response.headers['Content-Type'] = 'application/json'
		return response

	# Get user info
	userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
	params = {'access_token': access_token, 'alt': 'json'}
	answer = requests.get(userinfo_url, params=params)

	data = answer.json()

	# see if user exists, if it doesn't make a new one
	user_id = getUserID(data['email'])
	if not user_id:
		user_id = createUser(data)

	# set login_session data
	user = getUser(user_id)
	login_session['access_token'] = access_token
	login_session['gplus_id'] = gplus_id
	login_session['username'] = user.username
	login_session['email'] = user.email
	login_session['picture'] = user.picture
	login_session['user_id'] = user.id
	login_session['provider'] = 'google'
	response = make_response(json.dumps(user.serialize), 200)
	response.headers['Content-Type'] = 'application/json'
	return response


# Revoke google oauth token
def gdisconnect():
	# Only disconnect a connected user.
	access_token = login_session.get('access_token')
	if access_token is None:
		return 200
	url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
	h = httplib2.Http()
	result = h.request(url, 'GET')[0]
	if result['status'] == '200':
		# Reset the user's session.
		return 200
	else:
		# For whatever reason, the given token was invalid.
		return jsonify(message='Failed to revoke token for given user.'), 400


# umbrella disconnect function, signing out user regardless of service used
@app.route('/api/disconnect')
def disconnect():
	provider = login_session.pop('provider', None)
	if provider == 'google':
		gdisconnect()
	login_session.clear()
	return 'Successfully logged out.'


# register users without oauth
# TODO: allow picture upload or link
@app.route('/api/register', methods=['POST'])
def registerUser():
	email = request.form.get('email')
	if not re.match(email_pattern, email):
		return jsonify(message="Email is not valid."), 400
	password = request.form.get('password')
	username = request.form.get('username')
	if email == '' or password == '' or username == '':
		return jsonify(message='Form fields incomplete.'), 400
	if session.query(User).filter_by(email=email).first() is not None:
		return jsonify(message='User already registered.'), 400
	# initialize user
	user = User(email=email)
	user.username = username
	user.hash_password(password)
	user.picture = default_picture_url
	session.add(user)
	session.commit()

	# set login_session data
	user = session.query(User).filter_by(email=email).one()
	login_session['username'] = user.username
	login_session['email'] = user.email
	login_session['picture'] = user.picture
	login_session['user_id'] = user.id
	login_session['provider'] = 'none'
	return jsonify(message='You have successfully registered.'), 201

# login user without oauth
@app.route('/api/login', methods=['POST'])
def login():
	username = request.form.get('username')
	password = request.form.get('password')
	if username is None or password is None:
		return jsonify(message='Form fields incomplete.'), 400
	user = session.query(User).filter(User.username.like('%'+username+'%')).first()
	if user is None:
		return jsonify(message='User not registered.'), 400
	if not user.verify_password(password=password):
		return jsonify(message='Username or password incorrect.'), 400

	# set login_session data
	login_session['username'] = user.username
	login_session['email'] = user.email
	login_session['picture'] = user.picture
	login_session['user_id'] = user.id
	login_session['provider'] = 'none'
	return jsonify({'username': user.username, 'email': user.email, 'picture': user.picture, 'user_id': user.id}), 201

# get the user who is currently signed in.
@app.route('/api/userdata')
def getCurrentUser():
	if 'username' not in login_session:
		abort(403)
	else:
		user = session.query(User).filter_by(username=login_session['username']).first()
		return jsonify({'username': user.username, 'email': user.email, 'picture': user.picture, 'id': user.id}), 201


##########################
# CRUD Helpers
##########################

# check to see if name exists; returns a boolean
def validateExists(item):
	if item == '' or item is None:
		return False
	return True


# validate image url as an actual url
def validateImageUrl(img_url):
	# if user does not include an image url, send back a default
	if img_url == '':
		return 200, default_img_url, ''
	# if user includes img, check if valid.
	else:
		try:
			r = requests.get(img_url)
			msg = ''
			code = r.status_code
			print(code)
			if code == 404:
				msg = 'Image was not found. Enter a valid url or leave the field blank.'
				code = 400
			return code, img_url, msg
		except requests.exceptions.MissingSchema:
			return 400, '', 'Image url is an invalid schema. Enter a valid url or leave the field blank.'
		except requests.exceptions.InvalidSchema:
			return 400, '', 'Image url is missing schema. A preceding "http://" might fix it, or leave the field blank.'
		except:
			return 400, '', 'We don\'t know what\'s wrong with the entered image url, but please fix it.'


##########################
# User Helper
##########################

# register user with oauth
def createUser(login_session):
	newUser = User(name=login_session['name'], email=login_session['email'],
	               picture=login_session['picture'], username=login_session['name'])
	session.add(newUser)
	session.commit()
	user = session.query(User).filter_by(email=login_session['email']).one()
	return user.id

# get user object
def getUser(user_id):
	user = session.query(User).filter_by(id=user_id).one()
	return user

# get user ID
def getUserID(email):
	try:
		user = session.query(User).filter_by(email=email).one()
		return user.id
	except:
		flash("Failed to retrieve user ID.")
		return None


##########################
# Security Helpers
##########################

# Create 'STATE' strings
def generateRandomString():
	return ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(32))

# Generate a token for a cookie XSRF-prevention for registered users
def generateCsfrToken(user_id, expiration=1200):
	s = Serializer(app.config['SECRET_KEY'], expires_in=expiration)
	token = s.dumps({'id': user_id})
	login_session['_csrf_token'] = token
	return token

# Read in token
@app.before_request
def csrfProtectRead():
	if request.method == "POST" and request.endpoint != "gconnect":
		print('it popped')
		token = login_session.pop('_csrf_token', None)
		req_token = request.cookies.get('XSRF-TOKEN')  # or request.args.get('state')
		if not token or token != req_token:
			print('Token does not match.')
			abort(403)

# Set csrf token
@app.after_request
def crsfProtectWrite(resp):
	if 'username' not in login_session:
		token = generateRandomString()
	else:
		token = generateCsfrToken(login_session['user_id'])
	login_session['_csrf_token'] = token
	resp.set_cookie('XSRF-TOKEN', token.decode('ascii'))
	return resp


##########################
# Run application
##########################
if __name__ == '__main__':
	app.secret_key = 'Spray tans are so 1998.'
	app.debug = True
	app.run(host='0.0.0.0', port=8000)


