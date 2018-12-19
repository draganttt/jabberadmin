from configparser import ConfigParser
import psycopg2

def config(filename='database.ini', section='postgresql'):
	# create a parser
	parser = ConfigParser()
	# read config file
	parser.read(filename)

	# get section, default to postgresql
	db = {}
	if parser.has_section(section):
		params = parser.items(section)
		for param in params:
			db[param[0]] = param[1]
	else:
		raise Exception('Section {0} not found in the {1} file'.format(section, filename))
	return db

def connecttodb():
	try:
		params = config()
		con = psycopg2.connect(**params)
		print('+=========================+')
		print('|  CONNECTED TO DATABASE  |')
		print('+=========================+')
		return con.cursor()
	except:
		print('I am unable to connect to the database')
		con = '0'
		return con