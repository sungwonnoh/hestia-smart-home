/// 패키지(intl) 없이 쓰는 한국어 시간 표시.
abstract final class TimeFormat {
  static String hm(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  /// 목록용: 지금 / n분 전 / 18:32 / 어제 18:32 / 9월 28일 18:32
  static String relative(DateTime t, DateTime now) {
    final diff = now.difference(t);
    if (diff.inMinutes < 1) return '지금';
    if (diff.inMinutes < 60) return '${diff.inMinutes}분 전';
    final day = _dayDiff(t, now);
    if (day == 0) return hm(t);
    if (day == 1) return '어제 ${hm(t)}';
    return '${t.month}월 ${t.day}일 ${hm(t)}';
  }

  /// 상세용: 오늘 19:30 / 2026년 9월 30일 19:30
  static String full(DateTime t, DateTime now) {
    final day = _dayDiff(t, now);
    if (day == 0) return '오늘 ${hm(t)}';
    if (day == 1) return '어제 ${hm(t)}';
    return '${t.year}년 ${t.month}월 ${t.day}일 ${hm(t)}';
  }

  /// 알림 목록 그룹.
  static String group(DateTime t, DateTime now) {
    if (now.difference(t).inMinutes < 10) return '방금';
    return switch (_dayDiff(t, now)) {
      0 => '오늘',
      1 => '어제',
      _ => '이전',
    };
  }

  static int _dayDiff(DateTime t, DateTime now) {
    final a = DateTime(t.year, t.month, t.day);
    final b = DateTime(now.year, now.month, now.day);
    return b.difference(a).inDays;
  }
}
