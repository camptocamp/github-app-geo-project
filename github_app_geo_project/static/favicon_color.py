"""Used to generate different color favicons for the website."""

import cv2  # pylint: disable=import-error

if __name__ == "__main__":
    img = cv2.imread("github_app_geo_project/static/favicon-32x32.png", cv2.IMREAD_UNCHANGED)

    # red
    img[:, :, 0] = 0
    img[:, :, 1] = 0
    img[:, :, 2] = 255
    cv2.imwrite("github_app_geo_project/static/favicon-red-32x32.png", img)
    # green
    img[:, :, 0] = 0
    img[:, :, 1] = 255
    img[:, :, 2] = 0
    cv2.imwrite("github_app_geo_project/static/favicon-green-32x32.png", img)
    # gray
    img[:, :, 0] = 128
    img[:, :, 1] = 128
    img[:, :, 2] = 128
    cv2.imwrite("github_app_geo_project/static/favicon-gray-32x32.png", img)
    # blue
    img[:, :, 0] = 255
    img[:, :, 1] = 0
    img[:, :, 2] = 0
    cv2.imwrite("github_app_geo_project/static/favicon-blue-32x32.png", img)

    img = cv2.imread("github_app_geo_project/static/favicon-16x16.png", cv2.IMREAD_UNCHANGED)

    # red
    img[:, :, 0] = 0
    img[:, :, 1] = 0
    img[:, :, 2] = 255
    cv2.imwrite("github_app_geo_project/static/favicon-red-16x16.png", img)
    # green
    img[:, :, 0] = 0
    img[:, :, 1] = 255
    img[:, :, 2] = 0
    cv2.imwrite("github_app_geo_project/static/favicon-green-16x16.png", img)
    # gray
    img[:, :, 0] = 128
    img[:, :, 1] = 128
    img[:, :, 2] = 128
    cv2.imwrite("github_app_geo_project/static/favicon-gray-16x16.png", img)
    # blue
    img[:, :, 0] = 255
    img[:, :, 1] = 0
    img[:, :, 2] = 0
    cv2.imwrite("github_app_geo_project/static/favicon-blue-16x16.png", img)
