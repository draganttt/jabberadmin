import psycopg2
from flask import Flask, render_template, request
from lxml import etree
from settings import config, connecttodb


app = Flask(__name__)

@app.route("/")
@app.route("/index")
def index():
		return render_template('index.html')

@app.route('/rooms')
def rooms():
	cursor = connecttodb()
	if cursor == '0':
		tc_rooms = [('N/A@','','No connection to External DB','N/A','0','0')]
		rows_affected = 0
		return render_template('rooms.html', tc_rooms=tc_rooms, totalrooms=rows_affected)
	else:
		cursor.execute("select A.room_jid, A.creator_jid,A.subject,A.type,COALESCE(B.rocc,0) from tc_rooms A left join (select room_jid, count(room_jid) as rocc from tc_users where role != 'none' and role != 'mnone' group by room_jid) B on A.room_jid = B.room_jid order by B.rocc desc NULLS LAST")
		tc_rooms = cursor.fetchall()
		rows_affected = cursor.rowcount
		return render_template('rooms.html',tc_rooms=tc_rooms,totalrooms=rows_affected)


@app.route('/occupants')
def occupants():
	room = request.args.get('room')
	cursor = connecttodb()
	if cursor == '0':
		tc_users = [('N/A@','No connection to External DB','N/A','N/A','/N/A')]
		rows_affected = 0
		return render_template('occupants.html', tc_users=tc_users, room=room, totalocc=rows_affected)
	else:
		cursor.execute("select * from tc_users where room_jid = '%s' and role != 'none' and role != 'mnone' order by role desc" % room)
		tc_users = cursor.fetchall()
		rows_affected = cursor.rowcount
		return render_template('occupants.html', tc_users=tc_users, room=room,totalocc=rows_affected)

@app.route('/roomdetails')
def roomdetails():
	room = request.args.get('room')
	cursor = connecttodb()
	if cursor == '0':
		configDict = {}
		roomdetails = [('N/A@','N/A','No connection to External DB')]
		configDict['persistent'] = '0'
		configDict['fixed'] = 'No Connection to DB'
		return render_template('roomdetails.html', room=room, roomdetails=roomdetails, configDict=configDict)
	else:
		cursor.execute("select A.room_jid, A.creator_jid,A.subject,A.config,COALESCE(C.msg,0),COALESCE(B.rocc,0) from tc_rooms A left join (select room_jid, count(room_jid) as rocc from tc_users where role != 'none' and role != 'mnone' group by room_jid) B on A.room_jid = B.room_jid left join (select to_jid, count(to_jid) as msg from tc_msgarchive group by to_jid) C on A.room_jid = C.to_jid where A.room_jid = '%s' order by B.rocc desc NULLS LAST" % room)
		roomdetails = cursor.fetchall()
		roomconfig = roomdetails[0][3]
		configDict = {}
		if roomconfig is "":
			configDict['persistent'] = '0'
			configDict['fixed'] = 'Ad-Hoc (temporary) room. Note: Temporary chat rooms are automatically destroyed when all users leave the room'
			return render_template('roomdetails.html', room=room, roomdetails=roomdetails, configDict=configDict)
		else:
			tree = etree.fromstring(roomconfig)
			nodes = tree.xpath('p:field', namespaces={'p': 'jabber:x:data'})
			for r in nodes:
				for key in r.attrib:
					configDict[r.attrib[key]] = r[0].text if r[0].text is not None else "N/A"
			return render_template('roomdetails.html', room=room, roomdetails=roomdetails, configDict=configDict)


if  __name__ == "__main__":
	app.run(debug=True, host="0.0.0.0", port=8080)
