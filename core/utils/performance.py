import asyncio
import cProfile
import io
import logging
import os
import pstats
import time
import traceback

from contextlib import ContextDecorator

__all__ = [
    "PerformanceLog",
    "performance_log"
]


class PerformanceLog(ContextDecorator):
    def __init__(self, func_name: str, use_profiling: bool = False):
        self.func_name = func_name
        self.use_profiling = use_profiling
        self.logger = logging.getLogger('performance_log')

    def __enter__(self):
        self.start_time = time.time()
        if self.use_profiling:
            self.profiler = cProfile.Profile()
            self.profiler.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        execution_time = time.time() - self.start_time

        if self.use_profiling:
            self.profiler.disable()

            s = io.StringIO()
            sorted_stats = pstats.Stats(self.profiler, stream=s).sort_stats("cumulative")
            sorted_stats.print_stats(100)

            profiling_results = s.getvalue()
            profiling_results = '\n'.join(profiling_results.split('\n')[0:5])
            self.logger.info('Function {} profiling results:\n{}'.format(self.func_name, profiling_results))

        if exc_type is not None:
            tb_lines = traceback.format_exception(exc_type, exc_val, exc_tb)
            self.logger.error('An error occurred in {}: {}\n{}'.format(self.func_name, exc_val, ''.join(tb_lines)))
        elif execution_time > 1:
            self.logger.warning('{:.2f}s\t{}'.format(execution_time, self.func_name))
        elif execution_time != 0:
            self.logger.info('{:.2f}s\t{}'.format(execution_time, self.func_name))
        else:
            self.logger.debug('{:.2f}s\t{}'.format(execution_time, self.func_name))
        return False


def performance_log(use_profiling: bool = False):
    def decorator(func):

        if asyncio.iscoroutinefunction(func):
            async def wrapped(*args, **kwargs):
                log_name = f'{func.__qualname__}()'
                with PerformanceLog(log_name, use_profiling=use_profiling):
                    return await func(*args, **kwargs)
        else:
            def wrapped(*args, **kwargs):
                log_name = f'{func.__qualname__}()'
                with PerformanceLog(log_name, use_profiling=use_profiling):
                    return func(*args, **kwargs)
        return wrapped

    return decorator