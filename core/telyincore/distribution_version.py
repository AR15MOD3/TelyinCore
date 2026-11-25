"""Version policy for newly built Core distributions."""
import re


def validate_release_version(version):
    if not isinstance(version, str) or not re.fullmatch(
            r"beta\.(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}", version):
        raise ValueError("新发行版本号必须为 beta.x.y.z（三段数字）")
