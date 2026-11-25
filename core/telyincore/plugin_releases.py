"""One stable GitHub Release policy for discovery, updates and installation."""
from urllib.parse import quote

PAGE_SIZE = 20


def release_choice(repo, row):
    from .plugin_updates import Version
    if not isinstance(row, dict) or row.get('draft') or row.get('prerelease'):
        return None
    tag = row.get('tag_name')
    if not isinstance(tag, str) or len(tag) > 128:
        return None
    version = tag.removeprefix('v')
    try:
        if Version(version).pre:
            return None
    except ValueError:
        return None
    ref = quote(tag, safe='')
    return {'tag': tag, 'version': version, 'title': str(row.get('name') or tag)[:200],
            'date': row.get('published_at'), 'prerelease': False,
            'notes': str(row.get('body') or '')[:12000],
            'source': f'https://github.com/{repo}/releases/tag/{ref}',
            'download': f'https://github.com/{repo}/archive/refs/tags/{ref}.tar.gz'}


def release_page(repo, ask, page=1):
    data = ask(f'releases?per_page={PAGE_SIZE}&page={page}')
    if not isinstance(data, list):
        raise ValueError('GitHub 正式版本列表格式错误')
    rows = [choice for row in data if (choice := release_choice(repo, row))]
    return {'rows': rows, 'page': page, 'more': len(data) == PAGE_SIZE, 'kind': 'releases'}


def latest_release(repo, ask):
    for page in range(1, 6):
        result = release_page(repo, ask, page)
        if result['rows']:
            return result['rows'][0]
        if not result['more']:
            return None
    raise ValueError('暂未找到正式版本，请查看仓库发布记录')


def require_release(repo, tag, ask):
    row = ask('releases/tags/' + quote(tag, safe=''))
    choice = release_choice(repo, row)
    if not choice or choice['tag'] != tag:
        raise ValueError('所选版本不是正式 Release，不能从插件市场安装')
    return choice
