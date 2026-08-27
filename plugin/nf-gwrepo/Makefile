# Build the plugin
assemble:
	./gradlew assemble

clean:
	rm -rf .nextflow*
	rm -rf work
	rm -rf build
	./gradlew clean

# Run plugin unit tests
test:
	./gradlew test

# Install the plugin into the local nextflow plugins dir (~/.nextflow/plugins)
install:
	./gradlew install

# Publish the plugin
release:
	./gradlew releasePluginIfNotExists
