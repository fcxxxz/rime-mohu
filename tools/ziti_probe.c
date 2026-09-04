// 魔虎字提探针：用真实 librime 引擎查询候选列表/上屏结果。
// 与 tools/gen_ziti.py 配合使用（详见该脚本的用法说明）。
//
// 编译：
//   clang tools/ziti_probe.c -I/opt/homebrew/opt/librime/include \
//       -L/opt/homebrew/opt/librime/lib -lrime \
//       -Wl,-rpath,/opt/homebrew/opt/librime/lib -o /tmp/mohu-ziti/probe
//
// 用法:
//   probe <部署目录> <方案id>            查询模式：每行一个编码，输出 首页候选
//   probe <部署目录> <方案id> commit     回放模式：每行一串按键，输出 上屏文本
//   probe <部署目录> <方案id> neural     查询模式并打开 native 神经重排开关
//
// 部署目录需含方案全部 yaml/lua/opencc/gram 与 default.custom.yaml，
// 并把 *_fixed_legacy 方案一并列入 schema_list，否则 lua 翻译器加载
// legacy 词库时会报错导致四码/五码查询结果为空。
#include <rime_api.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char** argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: probe <user_data_dir> <schema_id> [commit]\n");
    return 2;
  }
  const char* user_dir = argv[1];
  const char* schema_id = argv[2];
  int commit_mode = (argc > 3 && strcmp(argv[3], "commit") == 0);
  int neural_mode = (argc > 3 && strcmp(argv[3], "neural") == 0);

  static const char* kModules[] = {"default", "plugins", NULL};
  RIME_STRUCT(RimeTraits, traits);
  // Keep the neural probe's userdb namespace separate from an active Squirrel
  // session.  Shared LevelDB locks can otherwise make a healthy rerank look
  // like a fail-open baseline during validation.
  traits.app_name = neural_mode ? "mohu-neural-probe" : "mohu-ziti-probe";
  traits.user_data_dir = user_dir;
  traits.shared_data_dir = user_dir;
  traits.log_dir = neural_mode ? "/tmp/mohu-neural-probe-logs" : "/tmp/mohu-ziti/logs";
  traits.modules = kModules;
  RimeApi* rime = rime_get_api();
  rime->setup(&traits);
  rime->initialize(&traits);

  if (rime->start_maintenance(1) != 1) {
    fprintf(stderr, "maintenance failed to start\n");
    return 3;
  }
  rime->join_maintenance_thread();
  fprintf(stderr, "maintenance done\n");

  RimeSessionId session = rime->create_session();
  if (!session) {
    fprintf(stderr, "failed to create session\n");
    return 4;
  }
  if (!rime->select_schema(session, schema_id)) {
    fprintf(stderr, "failed to select schema %s\n", schema_id);
    return 5;
  }
  if (neural_mode)

  char line[256];
  while (fgets(line, sizeof(line), stdin)) {
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
    if (n == 0) continue;

    rime->clear_composition(session);
    int unhandled = 0;
    for (size_t i = 0; i < n; i++) {
      int keycode = (int)(unsigned char)line[i];
      if (!rime->process_key(session, keycode, 0))
        unhandled = 1;
    }

    if (commit_mode) {
      RimeCommit commit;
      RIME_STRUCT_INIT(RimeCommit, commit);
      if (rime->get_commit(session, &commit)) {
        printf("%s\t%s\n", line, commit.text ? commit.text : "");
        rime->free_commit(&commit);
      } else {
        printf("%s\t\n", line);
      }
      rime->clear_composition(session);
      if (unhandled)
        fprintf(stderr, "unhandled key in: %s\n", line);
      continue;
    }

    RimeContext ctx;
    RIME_STRUCT_INIT(RimeContext, ctx);
    if (!rime->get_context(session, &ctx)) {
      fprintf(stderr, "no context for %s\n", line);
      continue;
    }
    printf("%s", line);
    int count = ctx.menu.num_candidates;
    if (count > 10)
      count = 10;
    for (int i = 0; i < count; i++) {
      const char* text = ctx.menu.candidates[i].text;
      printf("\t%s", text ? text : "");
    }
    printf("\n");
    rime->free_context(&ctx);
  }

  rime->destroy_session(session);
  rime->finalize();
  return 0;
}
