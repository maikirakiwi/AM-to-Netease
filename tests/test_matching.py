from music_sync.matching import choose, normalize
from music_sync.models import Candidate, Track


def test_normalize_handles_unicode_and_punctuation():
    assert normalize(" Ｊａｚｚ！ ") == "jazz"


def test_choose_accepts_unique_close_match():
    source = Track("a", "Song", "Artist", "Album", 180000)
    match, ranked = choose(source, [Candidate("n", "Song", "Artist", "Album", 180500)])
    assert match and match.id == "n"
    assert ranked[0].score >= 0.8


def test_choose_rejects_ambiguous_matches():
    source = Track("a", "Song", "Artist", "Album", 180000)
    candidates = [Candidate("1", "Song", "Artist", "Album", 180000), Candidate("2", "Song", "Artist", "Album", 180000)]
    match, _ = choose(source, candidates)
    assert match is None


def test_lyric_contains_ignores_timestamps_and_formatting():
    from music_sync.matching import contains_lyric_line
    assert contains_lyric_line('Ｈello, world!', '[00:01.20]Hello WORLD')
    assert contains_lyric_line('Hello world', '[00:01.20]Intro: Hello world — repeated')
    assert not contains_lyric_line('Hello world', '[00:01.20]Hello')
    assert not contains_lyric_line('', 'anything')
    assert not contains_lyric_line('hello world', 'hello\nworld')


def test_performer_sets_extract_cv_names_and_ignore_order():
    from music_sync.matching import artist_similarity, performers
    apple = Track('a', 'Song', '仙狐(CV:和氣あず未)、シロ(CV:内田真礼)')
    netease = Candidate('n', 'Song', '内田真礼 / 和氣あず未')
    assert artist_similarity(apple, netease) == 1
    assert performers('たまきとゆみね(CV:長縄まりあ・前川涼子)') == ['長縄まりあ', '前川涼子']
    assert performers('M・A・O / Artist / Artist') == ['M・A・O', 'Artist']
    assert artist_similarity(apple, Candidate('other', 'Song', 'Other')) == 0


def test_featured_credits_and_release_labels_score_across_fields():
    from music_sync.matching import score, title_credits
    apple = Track('a', '夢地図 (feat.GUMI)', '40mP', '夢地図 - Single', 180000)
    netease = Candidate('n', '夢地図', '40mP / GUMI', '夢地図', 180000)
    assert score(apple, netease) == 1
    assert title_credits('Song (featuring Guest) (Live)') == ('Song (Live)', ['Guest'])
    assert title_credits('Song feat.Guest [TV size]') == ('Song [TV size]', ['Guest'])
    assert title_credits('雪のように (with bless4)') == ('雪のように', ['bless4'])
    assert title_credits('With You') == ('With You', [])


def test_recording_versions_are_preserved():
    from music_sync.matching import title_credits, album_title
    for label in ('GAME VERSION', 'TV size', 'Live', 'instrumental', 'カラオケ'):
        title = f'Song ({label})'
        assert title_credits(title) == (title, [])
        assert album_title(title) == title
    assert album_title('Album - EP') == 'Album'
    assert album_title('Album - Single') == 'Album'


def test_search_queries_are_bounded_and_keep_version_labels():
    from music_sync.matching import search_queries
    source = Track('a', 'Song (feat. Guest) [TV size]', 'Character(CV:Actor) & Other')
    assert search_queries(source) == [
        'Song (feat. Guest) [TV size] Character(CV:Actor) & Other',
        'Song [TV size] Actor', 'Song [TV size]']
    assert search_queries(Track('a', 'Song', 'Artist')) == ['Song Artist', 'Song']


def test_qi_yue_qi_ri_qing_prefers_matching_album_over_first_compilation_result():
    source = Track('1442719419', '七月七日晴', '许慧欣', '美丽的爱情', 244973)
    candidates = [
        Candidate('307780', '七月七日晴', '许慧欣', '万中选一', 243893),
        Candidate('1900168811', '七月七日晴', '许慧欣', '心碎的声音', 243893),
        Candidate('307825', '七月七日晴', '许慧欣', '美丽的爱情', 244973),
    ]
    match, ranked = choose(source, candidates)
    assert match.id == '307825'
    assert ranked[0].score == 1.0


def test_cai_er_shi_san_can_match_album_release_instead_of_single():
    source = Track('1762980607', '才二十三', '方大同', '才二十三 - Single', 224499)
    match, _ = choose(source, [
        Candidate('2619125556', '才二十三', '方大同', '梦想家 The Dreamer', 224498),
        Candidate('cover', '才二十三 (Cover 方大同)', '陆屿森', '', 220134),
    ])
    assert match and match.id == '2619125556'


def test_exact_title_and_album_choose_candidate_regardless_of_artist_and_score():
    source = Track('a', 'Song (feat. Guest)', 'Apple artist', 'Album - Single', 180000)
    match, ranked = choose(source, [
        Candidate('other-album', 'Song', 'Apple artist / Guest', 'Other album', 180000),
        Candidate('exact', 'Ｓｏｎｇ！', 'Unrelated credit', 'Album', 180000),
    ])
    assert match.id == 'exact'
    assert match.score < 0.8
    assert ranked[0].id == 'other-album'


def test_exact_title_album_rule_keeps_duration_and_nonempty_checks():
    for source, candidate in [
        (Track('a', 'Song', 'Artist', 'Album', 180000), Candidate('n', 'Song', 'Other', 'Album', 210000)),
        (Track('a', 'Song', 'Artist', ''), Candidate('n', 'Song', 'Other', '')),
        (Track('a', '', 'Artist', 'Album'), Candidate('n', '', 'Other', 'Album')),
        (Track('a', 'Song (Live)', 'Artist', 'Album'), Candidate('n', 'Song', 'Other', 'Album')),
    ]:
        assert choose(source, [candidate])[0] is None


def test_exact_title_and_album_accept_unknown_duration_but_keep_multiple_matches_ambiguous():
    source = Track('a', 'Song', 'Artist', 'Album')
    first = Candidate('1', 'Song', 'Different artist', 'Album')
    second = Candidate('2', 'Song', 'Another artist', 'Album')
    assert choose(source, [first])[0].id == '1'
    assert choose(source, [first, second])[0] is None
