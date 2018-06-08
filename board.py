import os
import redis
from datetime import datetime
from urllib.parse import urlparse
from werkzeug.wrappers import Request, Response
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.wsgi import SharedDataMiddleware
from werkzeug.utils import redirect
from jinja2 import Environment, FileSystemLoader

def get_hostname(url):
    return urlparse(url).netloc

class Board(object):

    def __init__(self, config):
        self.redis = redis.Redis(config['redis_host'], config['redis_port'])
        template_path = os.path.join(os.path.dirname(__file__), 'templates')
        self.jinja_env = Environment(loader=FileSystemLoader(template_path), autoescape=True)
        self.jinja_env.filters['hostname'] = get_hostname
        self.url_map = Map([
            Rule('/', endpoint='main'),
            Rule('/new_adv', endpoint='new_adv'),
            Rule('/board:<board-id>', endpoint='information'),
            Rule('/add_comment:<board-id>', endpoint='add_comment')
        ])

    def render_template (self, template_name, **context):
        t = self.jinja_env.get_template(template_name)
        return Response(t.render(context), mimetype='text/html')

    def dispatch_request(self, request):
        adapter = self.url_map.bind_to_environ(request.environ)
        try:
            endpoint, values = adapter.match()
            return getattr(self, 'on_' + endpoint)(request, **values)
        except HTTPException as e:
            return e

    def on_new_adv(self, request):
        error = None
        if request.method == 'POST':
            creator = request.form['creator']
            board_name = request.form['board_name']
            if len(creator)>30:
                error = "You have only 30 symbols for username. Please enter correct username."
            elif len(board_name) > 30:
                error = "You have only 50 symbols for board name. Please enter correct board name."
            else:
                board_id = self.new_board(creator, board_name)
                return redirect('/%s+' % board_id)
        return self.render_template('new_board.html', error=error, creator = creator, board_name = board_name)

    def new_board(self, creator, board_name):
        board = self.redis.get('board:' + board_name)
        if board is not None:
            return board
        board_num = self.redis.incr('last_board_id')
        board = base36_encode(board_num)
        self.redis.set('board:' + board, board_name)
        self.redis.set('creator:board:' + board, creator)
        self.redis.set('date:board:' + board, datetime.now())
        return board

    def on_view_information(self, request, board_id):
        creator = self.redis.get("creator:board:" + board_id).decode("utf-8"),
        board_name = self.redis.get("board:" + board_id).decode("utf-8"),
        date = self.redis.get("date:board:" + board_id).decode("utf-8")
        return self.render_template('view_board.html', board_id=board_id, board_name=board_name, creator=creator, date=date, comment=self.get_comments(board_id))

    def on_add_comment(self, request, board_id):
        error = None
        if request.method == 'POST':
            creator = request.form['creator']
            comment = request.form['comment']
            if len(creator) > 30:
                error = 'You have only 30 symbols for username.'
            elif len(comment) > 255:
                error = 'You have only 255 symbols for comment.'
            else:
                self.insert_comment(request, board_id)
                return redirect('/board:' + board_id)
        return self.render_template('comment.html', error=error, creator=creator, comment=comment)

    def insert_comment(self, request, board_id):
        comment_id = self.redis.incr('last-comment-id:')
        comment = base36_encode(comment_id)
        self.redis.set('comment:' + comment, request.form['comment'])
        self.redis.set('creator:comment:' + comment, request.form['creator'])
        self.redis.lpush('comment:board:' + board_id, comment)
        return comment

    def get_comment(self, board_id):
        lenght = self.redis.llen('comment:board:' + board_id)
        keys = []
        for i in range(lenght):
            keys.append(self.redis.lindex('comment:board:' + board_id, i).decode('utf-8'))
        keys.sort()
        comment_array = []
        for key in keys:
            comment_array.append({
                'creator': self.redis.get('creator:comment:' + key).decode('utf-8'),
                'comment': self.redis.get('comment:' + key).decode('utf-8')
            })
        return comment_array

    def on_detail(self, request, board_id):
        detailed_info = {
            'creator': self.redis.get('creator:board:' + board_id).decode('utf-8'),
            'text': self.redis.get('board:' + board_id).decode('utf-8'),
            'time': self.redis.get('time:board:' + board_id).decode('utf-8'),
            'board_id': board_id
        }
        return self.render_template('view_board.html', detailed_info=detailed_info, comments=self.get_comments(board_id))

    def base36_encode(number):
        assert number >= 0, 'positive integer required'
        if number == 0:
            return '0'
        base36 = []
        while number != 0:
            number, i = divmod(number, 36)
            base36.append('0123456789abcdefghijklmnopqrstuvwxyz'[i])
        return ''.join(reversed(base36))

    def wsgi_app(self, environ, start_response):
        request = Request(environ)
        response = self.dispatch_request(request)
        return response(environ, start_response)

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)


def create_app(redis_host='localhost', redis_port=6379, with_static=True):
    app = Board({
        'redis_host':       redis_host,
        'redis_port':       redis_port
    })
    if with_static:
        app.wsgi_app = SharedDataMiddleware(app.wsgi_app, {
            '/static':  os.path.join(os.path.dirname(__file__), 'static')
        })
    return app

if __name__ == '__main__':
    from werkzeug.serving import run_simple
    app = create_app()
    run_simple('127.0.0.1', 5000, app, use_debugger=True, use_reloader=True)

