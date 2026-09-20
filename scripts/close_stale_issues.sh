#!/usr/bin/env bash
#
# 폐지된 "매일 확인 필요" 이슈를 한 번에 닫는다.
#
# 2026-09-20까지 daily-curation.yml은 큐레이션이 돈 날마다 review_notes.md를
# 그대로 이슈로 열었다. 매일 열리는 알림은 곧 아무도 읽지 않는 알림이 되므로
# 그 동작을 폐지했고(계약 "봇 동작" 4: 이슈는 작품 추가·배지 변경·은퇴·
# screening 중단 네 가지 때만), 남아 있는 열린 이슈를 정리하는 것이 이 스크립트다.
#
# 워크플로우에서 부르지 않는다. 사람이 한 번 손으로 돌리는 것이다.
#
#   scripts/close_stale_issues.sh --dry-run   # 무엇을 닫을지만 본다 (기본값)
#   scripts/close_stale_issues.sh --apply     # 실제로 닫는다
#
# gh CLI 로그인이 되어 있어야 한다.

set -euo pipefail

# 예전 워크플로우가 쓴 제목: "큐레이션 검토 필요 (yyyy-mm-dd)".
TITLE_PATTERN='^큐레이션 검토 필요'
COMMENT='자동 큐레이션의 매일 "확인 필요" 이슈는 폐지했습니다 (docs/screening-contract.md "봇 동작" 4). 이제 이슈는 작품 추가·배지 변경·프리미엄 은퇴·상영 수집 중단 네 가지 때만 열립니다.'

MODE="dry-run"
case "${1:-}" in
  --apply)   MODE="apply" ;;
  --dry-run|"") MODE="dry-run" ;;
  *) echo "사용법: $0 [--dry-run|--apply]" >&2; exit 64 ;;
esac

mapfile -t numbers < <(
  gh issue list --state open --limit 200 --json number,title \
    --jq ".[] | select(.title | test(\"$TITLE_PATTERN\")) | .number"
)

if [ "${#numbers[@]}" -eq 0 ]; then
  echo "닫을 이슈 없음 (제목 패턴: $TITLE_PATTERN)"
  exit 0
fi

echo "대상 ${#numbers[@]}건: ${numbers[*]}"
if [ "$MODE" = "dry-run" ]; then
  echo "--dry-run — 아무것도 닫지 않음. 실제로 닫으려면 --apply."
  exit 0
fi

for n in "${numbers[@]}"; do
  gh issue close "$n" --comment "$COMMENT"
  echo "닫음: #$n"
done
