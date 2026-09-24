APP := meetseen.app
VER := $(shell plutil -extract CFBundleShortVersionString raw app/Info.plist)

.PHONY: app dmg clean

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

# Dağıtım DMG'i: uygulamanın içine Python + venv + kaynaklar gömülür.
# Hedef makinede yalnız macOS 13+ (Apple Silicon) gerekir; ffmpeg ve Whisper
# modeli ilk kullanımda kontrol/iniş yapar.
dmg: app
	rm -rf dist dist-root
	mkdir -p dist-root
	cp -R $(APP) dist-root/
	@# --- kaynak dosyalar ---
	mkdir -p dist-root/$(APP)/Contents/Resources/appfiles/bin
	cp meetseen.py webui.py ocr_frames.swift requirements.txt venv-fixup.sh \
	   README.md LICENSE dist-root/$(APP)/Contents/Resources/appfiles/
	cp -R ui dist-root/$(APP)/Contents/Resources/appfiles/ui
	cp bin/ocr_frames dist-root/$(APP)/Contents/Resources/appfiles/bin/ocr_frames
	@# --- Python çifti: framework + venv (yeniden konumlanabilir; fixup ilk açılışta) ---
	@FW=$$(.venv/bin/python -c "import sys,os;print(os.path.dirname(os.path.dirname(sys.base_prefix)))"); \
	echo "Python framework: $$FW"; \
	mkdir -p dist-root/$(APP)/Contents/Resources/py-framework; \
	cp -R "$$FW" dist-root/$(APP)/Contents/Resources/py-framework/Python.framework
	rsync -a --exclude='__pycache__' --exclude='bin/python*' --exclude='.meetseen-fixed-path' \
	      .venv/ dist-root/$(APP)/Contents/Resources/appfiles/.venv/
	@# --- imza + dmg ---
	codesign --force --deep --sign - dist-root/$(APP)
	ln -s /Applications dist-root/Applications
	mkdir -p dist
	hdiutil create -volname meetseen -srcfolder dist-root \
	    -format UDZO -ov dist/meetseen-$(VER)-arm64.dmg
	@du -sh dist/meetseen-$(VER)-arm64.dmg | awk '{print "✔ DMG hazır: dist/"$$2}'

clean:
	cd app && (swift package reset || true)
	rm -rf $(APP) dist dist-root
