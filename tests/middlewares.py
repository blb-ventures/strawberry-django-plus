import cProfile


def profiler(get_response):
    def middleware(request):
        profiler = cProfile.Profile()
        profiler.enable()
        response = get_response(request)
        profiler.disable()
        profiler.print_stats(sort="cumtime")
        return response

    return middleware
