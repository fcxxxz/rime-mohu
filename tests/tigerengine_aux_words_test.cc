// Auxiliary constraints must preserve an existing word's identity, without
// generating combinations of spelling variants in the source dictionary.
#include "../tiger_sentence_native/tigerengine.cc"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <utility>

namespace {
template<typename T> void append(std::vector<uint8_t>& data,T value) {
  size_t n=data.size();data.resize(n+sizeof(value));std::memcpy(data.data()+n,&value,sizeof(value));
}
template<typename T> void put(std::vector<uint8_t>& data,size_t offset,T value) {
  std::memcpy(data.data()+offset,&value,sizeof(value));
}
std::vector<uint8_t> boundary_model(bool crossing_observed=false,bool inside_observed=true) {
  std::vector<uint8_t> data(104,0);std::memcpy(data.data(),"TCSKNM02",8);
  put(data,8,uint32_t{1});put(data,12,uint32_t{104});put(data,24,uint32_t{64});
  put(data,32,uint32_t{1});put(data,40,uint32_t{104});
  append(data,uint32_t{0});append(data,.1f);
  put(data,56,static_cast<uint64_t>(data.size()));put(data,64,static_cast<uint64_t>(data.size()));
  const uint64_t start=data.size();put(data,88,start);
  append(data,pack2(kBOS,0x6574));append(data,1.f);append(data,uint32_t{1});
  append(data,uint32_t{inside_observed?0x53e5u:0x65e0u});append(data,.4f);
  append(data,pack2(0x5728,0x6574));append(data,1.f);append(data,uint32_t{crossing_observed?2u:1u});
  append(data,uint32_t{0x5267});append(data,.25f);
  if(crossing_observed){append(data,uint32_t{0x53e5});append(data,.02f);}
  put(data,72,uint32_t{2});put(data,80,uint32_t{1});put(data,96,static_cast<uint64_t>(data.size()));
  append(data,pack2(kBOS,0x6574));append(data,start);
  put(data,16,static_cast<uint64_t>(data.size()));return data;
}
std::string decode(int h,const char*raw,bool fresh=true) {
  char out[65536]={};
  int n=fresh?tiger_decode_full(h,raw,0,out,sizeof(out)):tiger_decode(h,raw,0,out,sizeof(out),nullptr);
  return n>0?out:std::string{};
}
bool word_edge(int h,const std::string& text,size_t raw_end,size_t chars) {
  auto*e=g_engines[h].get();
  for(const auto& s:e->pool)if(s->text==text && s->raw_length==raw_end && s->previous &&
    s->text_length-s->previous->text_length==chars*3)return true;
  return false;
}
bool check(bool test,const char* message) {
  if(!test)std::cerr<<"fail: "<<message<<'\n';return test;
}
}
int main() {
  int rank=0;size_t selected_end=0;
  Engine::parse_selector("vgx2ju",3,&rank,&selected_end);
  bool selector_ok=check(rank==2 && selected_end==4,"numeric selectors include their first digit");
  Engine::parse_selector("vgx0ju",3,&rank,&selected_end);
  selector_ok&=check(rank==10 && selected_end==4,"single zero selects the tenth candidate");
  Engine::parse_selector("vgx12ju",3,&rank,&selected_end);
  selector_ok&=check(rank==12 && selected_end==5,"multi-digit selectors keep all digits");
  const auto root=std::filesystem::temp_directory_path()/
    ("tiger-aux-word-"+std::to_string(static_cast<long long>(std::filesystem::file_time_type::clock::now().time_since_epoch().count())));
  std::filesystem::create_directory(root);
  std::vector<uint8_t> bytes={'T','C','S','K','N','M','0','1'};
  append(bytes,uint32_t{1});append(bytes,uint32_t{1});append(bytes,uint32_t{0});append(bytes,.1f);
  append(bytes,uint64_t{0});append(bytes,int32_t{0});append(bytes,uint64_t{0});append(bytes,uint64_t{0});
  const auto model=root/"model.bin",lexicon=root/"lexicon.txt";
  {
    std::ofstream f(model,std::ios::binary);f.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());
    std::ofstream l(lexicon);
    l<<"vg\t整\t1\t100\nvgx\t整\t1\t100\nvgx\t争\t2\t100\n"
      "vgxy\t整\t1\t100\nvgy\t争\t1\t100\nju\t句\t8\t100\njud\t句\t8\t100\n"
      "xy\t影\t1\t100\n"
      "ju\t剧\t10\t100\nju\t车\t2\t100\njv\t句\t8\t100\t100\tju\n"
      "ie\t车\t1\t100\nvgju\t整句\t2\t100\nvgie\t整车\t1\t100\n"
      "wu\t无\t1\t100\ndi\t敌\t1\t100\nwudi\t无敌\t1\t100\n"
      "xm\t现\t1\t100\nzl\t在\t1\t100\nxmzl\t现在\t1\t100\n"
      "vgjuwu\t整句无\t1\t100\nvj\t整剧\t1\t100\n"
      "vguu\t整书\t99\t100\nuu\t书\t1\t100\nuud\t书\t1\t100\n";
  }
  char error[512]={};int h=tiger_engine_create(model.string().c_str(),lexicon.string().c_str(),200,1,error,sizeof(error));
  bool ok=selector_ok & check(h>=0,error);
  if(h>=0){
    tiger_engine_set_word_edge_weight(h,.5);
    decode(h,"xmzlvgxjuwudi");
    ok&=check(word_edge(h,"现在整句",9,2),"auxiliary first syllable keeps a whole-word edge inside a sentence");
    for(auto raw:{"vgxjudwudi","vgxyjudwudi","vgxjvwudi","vgjudwudi"}){
      decode(h,raw);size_t end=std::string(raw).size()-4;
      ok&=check(word_edge(h,"整句",end,2),"first/last/multiple auxiliary positions and canonical fly-key readings are matched");
    }
    decode(h,"vgxjuwudi");
    ok&=check(!word_edge(h,"整车",5,2),"same text with a different word pronunciation is never borrowed");
    decode(h,"vgyjuwudi");
    ok&=check(!word_edge(h,"整句",5,2),"a wrong auxiliary code cannot yield the word");
    decode(h,"vgxyjuwudi");
    ok&=check(!word_edge(h,"整句",6,2),"a complete ordinary syllable is not swallowed as two auxiliary keys");
    decode(h,"vgx2juwudi");
    ok&=check(!word_edge(h,"整句",6,2),"component selection keys remain authoritative");
    decode(h,"vgxjuwu");
    ok&=check(word_edge(h,"整句无",7,3),"three-character dictionary words use the same matcher");
    decode(h,"vgxuudwudi");
    ok&=check(!word_edge(h,"整书",6,2),"rank-99 injected words stay out of internal sentence edges");
    for(auto raw:{"v","vg","vgx","vgxj","vgxju","vgxjud","vgxjudwu","vgxjudwudi","vgxju","xmzlvgxjuwudi"}){
      auto incremental=decode(h,raw,false);
      ok&=check(incremental==decode(h,raw),"incremental append and shrink are identical to a full decode");
    }
    const auto before=decode(h,"vgxjuwudi");
    tiger_engine_set_personal_lexicon(h,"vgju\t整句\t3\n");
    auto learned=decode(h,"vgxjuwudi");
    ok&=check(!learned.empty() && word_edge(h,"整句",5,2),"index references survive a personal overlay refresh");
    tiger_engine_set_personal_lexicon(h,"");
    ok&=check(decode(h,"vgxjuwudi")==before,"personal overlay removal restores output exactly");
    tiger_engine_free(h);
  }
  {
    Bucket bucket;bucket.free_order=true;bucket.track_auxiliary_boundary=true;
    State used,unused;used.text=unused.text="相同文本";used.score=2;unused.score=1;
    used.auxiliary_boundary_gain=.25;bucket.add(&used);bucket.add(&unused);
    ok&=check(bucket.best.size()==2,"used and unused boundary budgets remain distinct beam states");
  }
  {
    const auto guarded_model=root/"boundary.bin";const auto data=boundary_model();
    std::ofstream f(guarded_model,std::ios::binary);f.write(reinterpret_cast<const char*>(data.data()),data.size());f.close();
    int gh=tiger_engine_create(guarded_model.string().c_str(),lexicon.string().c_str(),200,1,error,sizeof(error));
    ok&=check(gh>=0,error);
    if(gh>=0){
      tiger_engine_set_word_edge_weight(gh,.5);
      const auto output=decode(gh,"xmzlvgxjuwudi");
      const auto first=output.find('\n');
      const std::string expected="现在整句无敌\t";
      ok&=check(first!=std::string::npos && output.compare(first+1,expected.size(),expected)==0,
        "an unsupported cross-word window cannot override observed evidence inside a validated word");
      tiger_engine_set_auxiliary_context_guard(gh,0);
      const auto unguarded=decode(gh,"xmzlvgxjuwudi");
      const std::string other="现在整剧无敌\t";
      ok&=check(unguarded.compare(unguarded.find('\n')+1,other.size(),other)==0,
        "weight zero disables the boundary guard and restores the original ambiguous order");
      tiger_engine_set_auxiliary_context_guard(gh,.5);
      ok&=check(decode(gh,"xmzlvgxjuwudi")==output,"reenabling the guard restores output exactly");
      decode(gh,"xmzlvgxjuvgxjuwudi");
      for(const auto& state:g_engines[gh]->pool)
        ok&=check(state->auxiliary_boundary_gain<=1.0,"multiple protected words share one bounded path budget");
      ok&=check(tiger_engine_set_auxiliary_context_guard(gh,2)==-1,"invalid guard weight is rejected");
      ok&=check(tiger_engine_set_auxiliary_word_edges(gh,2)==-1,"invalid auxiliary switch is rejected");
      tiger_engine_set_auxiliary_word_edges(gh,0);decode(gh,"xmzlvgxjuwudi");
      ok&=check(!word_edge(gh,"现在整句",9,2),"disabling projection restores literal dictionary matching");
      tiger_engine_free(gh);
    }
    for(auto variant:{std::pair<bool,bool>{true,true},{false,false}}){
      const auto variant_model=root/"variant.bin";const auto fixture=boundary_model(variant.first,variant.second);
      std::ofstream vf(variant_model,std::ios::binary);vf.write(reinterpret_cast<const char*>(fixture.data()),fixture.size());vf.close();
      int vh=tiger_engine_create(variant_model.string().c_str(),lexicon.string().c_str(),200,1,error,sizeof(error));
      ok&=check(vh>=0,error);
      if(vh>=0){
        tiger_engine_set_word_edge_weight(vh,.5);auto guarded=decode(vh,"xmzlvgxjuwudi");
        tiger_engine_set_auxiliary_context_guard(vh,0);
        ok&=check(guarded==decode(vh,"xmzlvgxjuwudi"),
          "observed crossing context or missing word-internal evidence prevents fallback protection");
        tiger_engine_free(vh);
      }
    }
  }
  std::filesystem::remove_all(root);
  if(ok)std::cout<<"pass: auxiliary-aware dictionary words and matching guards\n";
  return ok?0:1;
}
