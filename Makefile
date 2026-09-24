APP := meetseen.app

.PHONY: app clean

# meetseen.app üretir: Swift kabuk derlenir, bundle oluşturulur, ad-hoc imzalanır.
app:
	cd app && swift build -c release
	rm -rf $(APP)
	mkdir -p $(APP)/Contents/MacOS
	cp app/.build/release/meetseen-app $(APP)/Contents/MacOS/
	cp app/Info.plist $(APP)/Contents/
	printf 'APPL????' > $(APP)/Contents/PkgInfo
	codesign --force --sign - $(APP)
	@echo "✔ $(APP) hazır — 'open $(APP)'"

clean:
	cd app && (swift package reset || true)
	rm -rf $(APP)
