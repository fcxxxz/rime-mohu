OCD2_FILES=\
	mohu_chaifen.ocd2 \
	mohu_dzing_variants.ocd2 \
	mohu_emoji.ocd2 \
	mohu_pinyinhint.ocd2 \
	mohu_TSCharacters.ocd2

all: $(OCD2_FILES)

%.ocd2: %.txt
	opencc_dict -i $< -o $@ -f text -t ocd2

clean:
	rm -f $(OCD2_FILES)

.PHONY: all clean
