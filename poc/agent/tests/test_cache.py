def test_graph_cache_checksum():
    from src.cache import GraphCache
    cache = GraphCache("/tmp/test-cache")
    checksum = cache.compute_checksum("test content")
    assert len(checksum) == 64  # SHA-256 produces 64 hex chars


def test_graph_cache_save_load():
    from src.cache import GraphCache
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = GraphCache(tmpdir)
        checksum = cache.compute_checksum("test")
        cache.save(checksum, {"graph": "data"})
        loaded = cache.load(checksum)
        assert loaded == {"graph": "data"}