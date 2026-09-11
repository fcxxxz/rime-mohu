#include "tigerengine.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>

int main() {
  const char* model = std::getenv("MOHU_SEMANTIC_MODEL");
  const char* vocab = std::getenv("MOHU_SEMANTIC_VOCAB");
  if (!model || !*model || !vocab || !*vocab) {
    std::puts("skip: set MOHU_SEMANTIC_MODEL and MOHU_SEMANTIC_VOCAB");
    return 0;
  }

  char error[512] = {0};
  int handle = tiger_semantic_create(model, vocab, error, sizeof(error));
  if (handle < 0) {
    std::fprintf(stderr, "semantic create failed: %s\n", error);
    return 1;
  }

  const char* candidates = "学生\n学说\n雪山";
  const double native_scores[] = {-3.43, -10.0, -11.3};
  double scores[3] = {0, 0, 0};
  int count = tiger_semantic_score(handle, "上课时", candidates, native_scores,
                                   3, scores);
  assert(count == 3);
  for (double score : scores) assert(std::isfinite(score));
  assert(scores[0] > scores[1]);
  assert(scores[0] > scores[2]);

  tiger_semantic_free(handle);
  assert(tiger_semantic_score(handle, "上课时", candidates, native_scores,
                              3, scores) == -1);
  std::puts("mohu semantic native scorer tests passed");
  return 0;
}
