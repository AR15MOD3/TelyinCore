import pytest
from telyincore.plugins import _one_plugin

@pytest.mark.parametrize('repository,expected',[
 ('https://github.com/owner/plugin','https://github.com/owner/plugin'),
 ({'type':'git','url':'git+https://github.com/owner/plugin.git'},'https://github.com/owner/plugin'),
 ('https://token@github.com/owner/plugin',''),
 ('https://github.com/owner/plugin?token=secret',''),
 ('file:///private/path',''),
 (None,''),
])
def test_repository_public_metadata(repository,expected):
 row=_one_plugin('demo',{'name':'demo','repository':repository},origin={'source':'local','url':''})
 assert row['repository']==expected
 assert row['origin']['source']=='local'  # Preserve actual install provenance.
