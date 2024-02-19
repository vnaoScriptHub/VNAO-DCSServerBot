import pandas as pd
from psycopg.rows import dict_row

from core import report


class NodeStats(report.MultiGraphElement):

    async def render(self, node: str, period: str):
        sql = """
            SELECT date_trunc('minute', time) AS time, pool_size, requests_waiting, requests_wait_ms, workers
            FROM nodestats 
            WHERE time > ((NOW() AT TIME ZONE 'UTC') - ('1 ' || %s)::interval)
            AND node = %s 
            ORDER BY 1
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, (period, node))
                if cursor.rowcount > 0:
                    series = pd.DataFrame.from_dict(await cursor.fetchall())
                    series.columns = ['time', 'DB Pool', 'Waiting (Req)', 'Waiting (ms)', 'Worker Threads']
                    series.plot(ax=self.axes[0], x='time', y=['DB Pool'], title='DB Pool Size', xticks=[], xlabel='')
                    self.axes[0].legend(loc='upper left')
                    series.plot(ax=self.axes[1], x='time', y=['Waiting (Req)'], title='Waiting', xticks=[], xlabel='')
                    self.axes[1].legend(loc='upper left')
                    ax3 = self.axes[1].twinx()
                    series.plot(ax=ax3, x='time', y=['Waiting (ms)'], xticks=[], xlabel='', color='red')
                    ax3.legend(['Waiting (ms)'], loc='upper right')
                    series.plot(ax=self.axes[2], x='time', y=['Worker Threads'], title='Worker Threads', xlabel='',
                                ylabel='Threads')
                else:
                    for i in range(0, 2):
                        self.axes[i].bar([], [])
                        self.axes[i].set_xticks([])
                        self.axes[i].set_yticks([])
                        self.axes[i].text(0, 0, 'No data available.', ha='center', va='center', size=20)
