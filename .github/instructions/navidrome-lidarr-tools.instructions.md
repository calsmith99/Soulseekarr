---
applyTo: '**'
---
You are developing a project that runs on an ubuntu server in docker. The project consists of a Flask web application that provides a user interface for managing music files and interacting with Navidrome, Lidarr, and slskd. The application is designed to be easily deployable in a Docker container, with all necessary dependencies included in the container image.

The best way to deploy this project will be using Docker Compose, which allows you to define and manage multi-container Docker applications. The project should include a `docker-compose.yml` file that specifies the services, networks, and volumes required for the application to run. We should use env variables from the portainer stack env for configuration in scripts

The mount directories for the Docker container should be as follows:
volumes:
    - :/media/Owned
    - :/media/Not_Owned
    - :/media/Incomplete
    - :/downloads/completed
    - :/data
    - :/logs:rw

When interacting with navidrome, we should be using the subsonic api.
