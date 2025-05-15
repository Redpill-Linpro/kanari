.PHONY: all clean docker install_deps run serve

all:
	@echo "make serve       - build the Docker img and serve w/ db on port 8080 (prod)"
	@echo "make servebg     - build the Docker img and serve detached w/ db on port 8080 (prod)"
	@echo "make serve_no_db - build the Docker img and serve without db on port 8080 (prod)"
	@echo "make docker      - only build the Docker image"
	@echo "make run         - install deps with pip and serve as dev on port 5000"
	@echo "make clean       - remove temporary files"

run: install_deps
	python3 -m flask --app wsgi.py run

install_deps:
	python3 -m pip install --no-cache-dir -r requirements.txt

docker_build_no_cache:
	docker build -t kanari . --no-cache

docker_compose_build_no_cache:
	docker-compose build --no-cache

docker_build:
	docker build -t kanari .

docker_compose_build:
	docker-compose build

serve: docker_compose_build
	docker-compose up

serve_no_cache: docker_compose_build_no_cache
	docker-compose up

servebg: docker_compose_build
	docker-compose up -d

servebg_no_cache: docker_compose_build_no_cache
	docker-compose up -d

serve_no_db: docker_build
	docker run -p 8080:8080 kanari

serve_no_db_no_cache: docker_build_no_cache
	docker run -p 8080:8080 kanari

clean:
	rm -frv __pycache__
