PYEOFrint(f"{label:<35} {nok:>12,} {pct:>+7.2f}% {batches:>8} {stopped:>8} {skipped:>8}"):>8}")
Period: 2026-01-02 → 2026-05-04  |  Limit: Day0 close +1%

Config                                       NOK   Return  Batches  Stopped  Skipped
----------------------------------------------------------------------------------
No stop, no limit                        112,216  +12.22%       16        0        0
3% stop, no limit                        129,039  +29.04%       16       28        0
No stop, +1% limit                        91,122   -8.88%       15        0       46
3% stop, +1% limit                       102,470   +2.47%       15       29       46
5% stop, +1% limit                        96,717   -3.28%       15       15       46
@svehbak-lab ➜ /workspaces/sentiment (main) $ 
