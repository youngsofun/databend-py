from urllib.parse import urlparse, parse_qs, unquote
from time import time
from databend_driver.connection import Connection
from databend_driver.util.helper import asbool
from databend_driver.result import QueryResult
import json


class Client(object):
    """
    Client for communication with the databend http server.
    Single connection is established per each connected instance of the client.
    """

    def __init__(self, *args, **kwargs):
        self.settings = (kwargs.pop('settings', None) or {}).copy()
        self.connection = Connection(*args, **kwargs)
        self.query_result_cls = QueryResult

    def __enter__(self):
        return self

    def disconnect(self):
        self.disconnect_connection()

    def disconnect_connection(self):
        self.connection.disconnect()

    def data_generator(self, raw_data):

        while raw_data['next_uri'] is not None:
            try:
                raw_data = self.receive_data(raw_data['next_uri'])
                if not raw_data:
                    break
                yield raw_data

            except (Exception, KeyboardInterrupt):
                self.disconnect()
                raise

    def receive_data(self, next_uri: str):
        resp = self.connection.next_page(next_uri)
        raw_data = json.loads(json.loads(resp.content))
        self.connection.check_error(raw_data)
        return raw_data

    def receive_result(self, query, query_id=None, with_column_types=False):
        raw_data = self.connection.query(query, None)
        self.connection.check_error(raw_data)
        columns_types = []
        fields = raw_data["schema"]["fields"]
        for field in fields:
            columns_types.append(field["data_type"]["type"])
        if raw_data['next_uri'] is None and with_column_types:
            return raw_data['data'], columns_types
        elif raw_data['next_uri'] is None:
            return raw_data['data']

        gen = self.data_generator(raw_data)
        result = self.query_result_cls(
            gen, with_column_types=with_column_types)
        return result.get_result()

    def iter_receive_result(self, query, with_column_types=False):
        raw_data = self.connection.query(query, None)
        self.connection.check_error(raw_data)
        if raw_data['next_uri'] is None:
            return raw_data
        gen = self.data_generator(raw_data)
        result = self.query_result_cls(
            gen, with_column_types=with_column_types)
        for rows in result.get_result():
            for row in rows:
                yield row

    def execute(self, query, params=None, with_column_types=False,
                query_id=None, settings=None):
        """
        Executes query.

        Establishes new connection if it wasn't established yet.
        After query execution connection remains intact for next queries.
        If connection can't be reused it will be closed and new connection will
        be created.

        :param query: query that will be send to server.
        :param params: substitution parameters for SELECT queries and data for
                       INSERT queries. Data for INSERT can be `list`, `tuple`
                       or :data:`~types.GeneratorType`.
                       Defaults to ``None`` (no parameters  or data).
        :param with_column_types: if specified column names and types will be
                                  returned alongside with result.
                                  Defaults to ``False``.
        :param query_id: the query identifier. If no query id specified
                         ClickHouse server will generate it.
        :param settings: dictionary of query settings.
                         Defaults to ``None`` (no additional settings).

        :return: * number of inserted rows for INSERT queries with data.
                   Returning rows count from INSERT FROM SELECT is not
                   supported.
                 * if `with_column_types=False`: `list` of `tuples` with
                   rows/columns.
                 * if `with_column_types=True`: `tuple` of 2 elements:
                    * The first element is `list` of `tuples` with
                      rows/columns.
                    * The second element information is about columns: names
                      and types.
        """

        rv = self.process_ordinary_query(
            query, params=params, with_column_types=with_column_types,
            query_id=query_id)
        return rv

    def process_ordinary_query(self, query, params=None, with_column_types=False,
                               query_id=None):
        return self.receive_result(query, query_id=query_id, with_column_types=with_column_types, )

    @classmethod
    def from_url(cls, url):
        """
        Return a client configured from the given URL.

        For example::

            http://[user:password]@localhost:9000/default
            http://[user:password]@localhost:9440/default

        Any additional querystring arguments will be passed along to
        the Connection class's initializer.
        """
        url = urlparse(url)

        settings = {}
        kwargs = {}

        host = url.hostname
        port = url.port if url.port is not None else 443

        if url.port is not None:
            kwargs['port'] = url.port
            port = url.port

        path = url.path.replace('/', '', 1)
        if path:
            kwargs['database'] = path

        if url.username is not None:
            kwargs['user'] = unquote(url.username)

        if url.password is not None:
            kwargs['password'] = unquote(url.password)

        if url.scheme == 'https':
            kwargs['secure'] = True

        for name, value in parse_qs(url.query).items():
            if not value or not len(value):
                continue
            if url.scheme == 'https':
                kwargs['secure'] = True

            timeouts = {
                'connect_timeout',
                'send_receive_timeout',
                'sync_request_timeout'
            }

            value = value[0]

            if name == 'client_name':
                kwargs[name] = value
            elif name == 'secure':
                kwargs[name] = asbool(value)
            elif name in timeouts:
                kwargs[name] = float(value)
            else:
                settings[name] = value

        if settings:
            kwargs['settings'] = settings

        return cls(host, **kwargs)
