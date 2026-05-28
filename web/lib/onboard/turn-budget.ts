export function shouldForceFinalize(
  startTimeMs: number,
  loopCount: number,
  maxLoopCount: number,
  budgetMs: number,
  nowMs: number = Date.now()
): boolean {
  if (nowMs - startTimeMs >= budgetMs) {
    return true;
  }
  // If we are at or exceeding maxLoopCount - 1, force finalize so the next iteration
  // wraps things up rather than getting cut off completely.
  if (loopCount >= maxLoopCount - 1) {
    return true;
  }
  return false;
}
