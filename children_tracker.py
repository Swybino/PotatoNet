import os
import cv2
import config
import tensorflow as tf
import numpy as np
import utils
import argparse
import logging
from PIL import Image
import matplotlib.image as mpimg
import visualization
import face_alignment
from faceAlignment.face_alignment.api import FaceAlignment, LandmarksType

from PyramidBox.preprocessing import ssd_vgg_preprocessing
from PyramidBox.nets.ssd import g_ssd_model
import PyramidBox.nets.np_methods as np_methods
from MemTrack.tracking.tracker import Tracker, Model

# TensorFlow session: grow memory when needed.
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
gpu_options = tf.GPUOptions(allow_growth=True)
conf = tf.ConfigProto(log_device_placement=False, gpu_options=gpu_options)
isess = tf.InteractiveSession(config=conf)

# Input placeholder.
data_format = 'NHWC'
img_input = tf.placeholder(tf.uint8, shape=(None, None, 3))
# Evaluation pre-processing: resize to SSD net shape.
image_pre, labels_pre, bboxes_pre, bbox_img = ssd_vgg_preprocessing.preprocess_for_eval(
    img_input, None, None, data_format, resize=ssd_vgg_preprocessing.Resize.NONE)
image_4d = tf.expand_dims(image_pre, 0)

# Define the SSD model.
predictions, localisations, _, end_points = g_ssd_model.get_model(image_4d)

# Restore SSD model.
ckpt_filename = 'PyramidBox/model/pyramidbox.ckpt'

isess.run(tf.global_variables_initializer())
saver = tf.train.Saver()
saver.restore(isess, ckpt_filename)

config_proto = tf.ConfigProto()
config_proto.gpu_options.allow_growth = True

logging.basicConfig(level=logging.DEBUG)


class MainTracker:
    def __init__(self, video_name):
        self.visualizer = visualization.VisualizerOpencv()
        self.face_aligner = face_alignment.FaceAligner()
        self.trackers_list = {}
        self.fa = FaceAlignment(LandmarksType._3D, device='cuda:0', flip_input=True)
        # Load the video sequence
        self.s_frames = load_seq_video(video_name)
        self.out_dir = os.path.join(config.out_dir, video_name[:-4])
        if not os.path.exists(self.out_dir):
            os.mkdir(self.out_dir)
        self.data = {}
        self.temp_track = {}
        self.angular_order = []

    def start_tracking(self):
        # # Detect faces in the first image
        img = mpimg.imread(self.s_frames[0])
        img = np.array(img)

        # _, _, rbboxes = detect_faces(img)
        # bboxes_list = [utils.reformat_bbox_coord(bbox, img.shape[0]) for bbox in rbboxes]
        #
        # # Let the user choose which face to follow
        # self.visualizer.prepare_img(img, 0)
        # _, bboxes_list, names_list = self.visualizer.select_bbox(bboxes_list)
        # for idx, name in enumerate(names_list):
        #     self.temp_track[name] = {config.BBOX_KEY: bboxes_list[idx]}
        #     self.data[name] = {config.BBOX_KEY: []}
        # # print(self.data, self.temp_track)
        # self.merge_temp()
        # print(self.temp_track)

        self.temp_track = {'a': {'bbox': [300, 183, 57, 49]}, 'b': {'bbox': [139, 201, 53, 45]},
                           'c': {'bbox': [94, 296, 77, 98]}, 'd': {'bbox': [317, 472, 48, 63]},
                           'e': {'bbox': [427, 443, 61, 43]}, 'f': {'bbox': [421, 230, 63, 39]}}
        self.data = {'a': {'bbox': [[300, 183, 57, 49]]}, 'b': {'bbox': [[139, 201, 53, 45]]}, 'c': {'bbox': [[94,
                                                                                                               296, 77,
                                                                                                               98]]},
                     'd': {'bbox': [[317, 472, 48, 63]]}, 'e': {'bbox': [[427, 443, 61, 43]]}, 'f': {'bbox': [[
                421, 230, 63, 39]]}}

        angles_dict = utils.get_bbox_dict_ang_pos(self.temp_track, img)
        for name in sorted(angles_dict, key=angles_dict.get):
            self.angular_order.append(name)

        print(self.angular_order)

        # Run the tracking process
        self.track_all()

    def track_all(self):
        with tf.Graph().as_default(), tf.Session(config=config_proto) as sess:
            model = Model(sess)
            for name, data in self.data.items():
                tracker = Tracker(model)
                tracker.initialize(self.s_frames[1], data[config.BBOX_KEY][0])
                self.trackers_list[name] = tracker

            frame_idx = 1
            while frame_idx < len(self.s_frames):
                self.temp_track = {}
                last_frame = min(frame_idx + config.checking_treshold, len(self.s_frames))

                for idx in range(frame_idx, last_frame):
                    logging.info("Processing frame {}".format(idx))
                    for name, tracker in self.trackers_list.items():
                        tracker.idx = idx
                        bbox, cur_frame = tracker.track(self.s_frames[idx])
                        self.temp_track[name] = {config.BBOX_KEY: bbox}
                        cur_frame = cur_frame * 255
                        self.visualizer.prepare_img(cur_frame, idx)

                    # Check the overlay every frame
                    ok, issues = self.check_overlay()
                    if not ok:
                        self.correct_overlay(issues)
                    if idx != last_frame - 1:
                        self.visualizer.plt_img(self.temp_track)
                        self.visualizer.save_img(self.out_dir)

                        self.merge_temp()

                # Check if the bbox is a face
                frame_idx = last_frame
                self.check_faces(cur_frame)
                self.merge_temp()
                # Visualization
                self.visualizer.plt_img(self.temp_track)
                self.visualizer.save_img(self.out_dir)
            return

    def merge_temp(self):
        for name, data in self.temp_track.items():
            self.data[name][config.BBOX_KEY].append(list(self.temp_track[name][config.BBOX_KEY]))

    def track(self, tracker, first_frame=1, last_frame=1):
        """
        Tracks a single face from first_frame to last_frame
        :param tracker:
        :param first_frame:
        :param last_frame:
        :return:
        """
        landmarks_list = []
        bbox_list = []
        with tf.Graph().as_default(), tf.Session(config=config_proto) as sess:
            for idx in range(first_frame, last_frame):
                tracker.idx = idx
                bbox, cur_frame = tracker.track(self.s_frames[idx])
                bbox_list.append([int(i) for i in bbox])
                cur_frame = cur_frame * 255

                landmarks = None

                # img_cropped_fd, crop_coord_fd = utils.crop_roi(bbox, cur_frame, 1.4)
                # face_rot, angle = utils.rotate_roi(img_cropped_fd, bbox, cur_frame.shape[0])
                # landmarks = self.fa.get_landmarks(face_rot)
                # if landmarks is not None :
                #     landmarks = utils.landmarks_img_coord(utils.rotate_landmarks(landmarks[-1], face_rot, -angle), crop_coord_fd)
                # landmarks_list.append(landmarks)

                # visualization
                self.visualizer.plt_img(cur_frame, [bbox], landmarks=landmarks)

        return bbox_list, landmarks_list

    def check_overlay(self):
        issues = []
        checked = []
        for name, data in self.temp_track.items():
            for name2, data2 in self.temp_track.items():
                if name != name2 and name2 not in checked and \
                        (utils.bb_intersection_over_union(data[config.BBOX_KEY], data2[config.BBOX_KEY]) > config.overlay_threshold or utils.bb_contained(data[config.BBOX_KEY], data2[config.BBOX_KEY])):
                    logging.info("Overlay issue between {}:{} and {}:{}".format(name, data[config.BBOX_KEY], name2,
                                                                                data2[config.BBOX_KEY]))
                    issues.append((name, name2))
            checked.append(name)

        if len(issues) == 0:
            return True, None
        else:
            return False, issues

    def correct_overlay(self, issues):
        for issue in issues:
            name1 = issue[0]
            name2 = issue[1]
            data1 = self.temp_track[name1]
            data2 = self.temp_track[name2]

            bbox1 = data1[config.BBOX_KEY]
            bbox2 = data2[config.BBOX_KEY]
            prev_bbox1 = self.data[name1][config.BBOX_KEY][-1]
            prev_bbox2 = self.data[name2][config.BBOX_KEY][-1]

            iou1 = utils.bb_intersection_over_union(prev_bbox1, bbox1)
            iou2 = utils.bb_intersection_over_union(prev_bbox2, bbox2)

            if iou1 == iou2 == 0:
                self.temp_track[name1][config.BBOX_KEY] = prev_bbox1
                self.temp_track[name2][config.BBOX_KEY] = prev_bbox2
                self.trackers_list[name1].redefine_roi(prev_bbox1)
                self.trackers_list[name2].redefine_roi(prev_bbox2)

            elif iou1 > iou2:
                self.temp_track[name2][config.BBOX_KEY] = prev_bbox2
                self.trackers_list[name2].redefine_roi(prev_bbox2)
            else:
                self.temp_track[name1][config.BBOX_KEY] = prev_bbox1
                self.trackers_list[name1].redefine_roi(prev_bbox1)
        return

    def check_faces(self, img):
        corrected_bbox = {}
        _, _, rbboxes = detect_faces(img, select_threshold=0.9)

        if len(rbboxes) == 0:
            return
        bbox_fd_list = [utils.reformat_bbox_coord(bbox, img.shape[0]) for bbox in rbboxes]

        for bbox_fd in bbox_fd_list:
            if bbox_fd[2] < config.min_bbox_size or bbox_fd[3] < config.min_bbox_size:
                bbox_fd_list.remove(bbox_fd)

        # Draw detected faces
        for bbox_fd in bbox_fd_list:
            vizu = [bbox_fd[0] - 2, bbox_fd[1] - 2, bbox_fd[2]+4, bbox_fd[3]+4]
            self.visualizer.draw_bbox(vizu, color=(0, 125, 255), thickness=2)
            self.visualizer.plt_img({})

        indices = []
        for idx, bbox_fd in enumerate(bbox_fd_list):
            for name, data in self.temp_track.items():
                bbox = data[config.BBOX_KEY]
                if utils.bb_intersection_over_union(bbox, bbox_fd) > config.overlay_threshold \
                        and self.check_angular_position(name, bbox_fd, img):
                    self.correct_tracker(name, bbox_fd)
                    corrected_bbox[name] = {config.BBOX_KEY: bbox_fd}
                    indices.append(idx)
                    break

        bbox_fd_list = [i for j, i in enumerate(bbox_fd_list) if j not in indices]
        indices = []
        # Draw ROI
        for name, data in self.temp_track.items():
            bbox = data[config.BBOX_KEY]
            xmin, ymin, xmax, ymax = utils.get_roi(bbox, img)
            roi = [xmin, ymin, xmax-xmin, ymax-ymin]
            self.visualizer.draw_bbox(roi, color=(150, 0, 0))

        for idx, bbox_fd in enumerate(bbox_fd_list):
            for name, data in self.temp_track.items():
                bbox = data[config.BBOX_KEY]
                if utils.bbox_in_roi(bbox, bbox_fd, img) and self.check_angular_position(name, bbox_fd, img):
                    self.correct_tracker(name, bbox_fd)
                    corrected_bbox[name] = {config.BBOX_KEY: bbox_fd}
                    indices.append(idx)
                    break

        bbox_fd_list = [i for j, i in enumerate(bbox_fd_list) if j not in indices]
        if len(bbox_fd_list) == 0:
            return

        corrected_bbox_angles_tmp = utils.get_bbox_dict_ang_pos(corrected_bbox, img)
        corrected_bbox_angles = {}

        for name in self.angular_order:
            if name in corrected_bbox_angles_tmp.keys():
                corrected_bbox_angles[name] = corrected_bbox_angles_tmp[name]

        not_corrected_bbox_angles = {k: utils.get_bbox_angular_pos(v[config.BBOX_KEY], img) for k, v in
                                     self.temp_track.items() if k not in corrected_bbox}
        if not not_corrected_bbox_angles:
            return

        for bbox_fd in bbox_fd_list:
            angle = utils.get_bbox_angular_pos(bbox_fd, img=img)
            if corrected_bbox_angles:
                prev_id, next_id = None, None
                for name, value in corrected_bbox_angles.items():
                    if angle > value:
                        prev_id = name
                        break
                for name, value in corrected_bbox_angles.items():
                    if angle < value:
                        next_id = name
                        break
                if prev_id is None:
                    prev_id = list(corrected_bbox_angles.keys())[-1]
                if next_id is None:
                    next_id = list(corrected_bbox_angles.keys())[0]
                ang_order = self.angular_order * 2
                start = ang_order.index(prev_id)
                end = ang_order.index(next_id, start + 1)
                potential_id_list = [i for i in ang_order[start + 1:end]]
            else:
                potential_id_list = self.angular_order

            if len(potential_id_list) == 1 and self.check_angular_position(potential_id_list[0], bbox_fd, img):
                self.correct_tracker(potential_id_list[0], bbox_fd)
                logging.info("Assigned {} to children {}".format(bbox_fd, potential_id_list[0]))
            elif len(potential_id_list) == 0:
                break
            else:
                key, value = min(not_corrected_bbox_angles.items(), key=lambda kv: abs(kv[1] - angle))
                if self.check_angular_position(key, bbox_fd, img):
                    self.correct_tracker(key, bbox_fd)
                logging.info("Assigned {} to children {} by closest angular position".format(bbox_fd, key))

    def correct_tracker(self, name, bbox):
        self.temp_track[name][config.BBOX_KEY] = bbox
        self.trackers_list[name].redefine_roi(bbox)

    def check_face(self, bbox, fd_bbox_list, name):
        corrected_bbox = []
        for bbox_fd in fd_bbox_list:
            if utils.bb_intersection_over_union(bbox, bbox_fd) > config.overlay_threshold:
                return False, bbox_fd
            elif ((name2 != name and utils.bb_intersection_over_union(self.temp_track[name2][config.BBOX_KEY],
                                                                      bbox_fd) < config.overlay_threshold) for name2 in
                  self.temp_track):
                corrected_bbox.append(bbox_fd)

        if len(corrected_bbox) == 0:
            return True, None
        elif len(corrected_bbox) > 1:
            # TODO: Do something if several corrections possible
            logging.warning("Several correction possible")
            return False, corrected_bbox[0]
        else:
            return False, corrected_bbox[0]

    def check_angular_position(self, name, bbox, img):
        angle = utils.get_bbox_angular_pos(bbox, img)
        l = len(self.angular_order)
        ang_order = self.angular_order * 2
        idx = ang_order.index(name)
        prev = self.angular_order[(idx - 1) % l]
        next = self.angular_order[(idx + 1) % l]
        angles_dict = utils.get_bbox_dict_ang_pos(self.temp_track, img)
        start, end = angles_dict[prev], angles_dict[next]
        end = end - start + 360 if (end - start) < 0 else end - start
        mid = angle - start + 360 if (angle - start) < 0 else angle - start
        result = 0 < mid < end
        return result
    
    def check_angular_order(self, img):
        angles_dict = utils.get_bbox_dict_ang_pos(self.temp_track, img)
        tmp_order = []
        for name in sorted(angles_dict, key=angles_dict.get):
            tmp_order.append(name)
        start = self.angular_order.index(tmp_order[0])
        l = len(self.angular_order)
        for i, n in enumerate(tmp_order):
            if n != self.angular_order[(start+i)%l]:
                return False
        return True

# Main image processing routine.
def load_seq_video(video_name):
    cap = cv2.VideoCapture(os.path.join(config.video_dir, video_name))
    img_dir_path = os.path.join(config.img_path, video_name[:-4])
    if not os.path.exists(img_dir_path):
        os.mkdir(img_dir_path)

    # Check if camera opened successfully
    if cap.isOpened() is False:
        print("Error opening video stream or file")

    # Read until video is completed
    frm_count = 0
    while cap.isOpened() and frm_count < 5000:
        # Capture frame-by-frame
        ret, frame = cap.read()
        if ret:
            # Display the resulting frame
            img_write_path = os.path.join(img_dir_path, "%05d.jpg" % frm_count)
            if not os.path.exists(img_write_path):
                cv2.imwrite(img_write_path, frame)
            frm_count += 1
        # Break the loop
        else:
            break

    # When everything done, release the video capture object
    cap.release()

    img_names = sorted(os.listdir(img_dir_path))
    s_frames = [os.path.join(img_dir_path, img_name) for img_name in img_names]

    return s_frames


def detect_faces(img, select_threshold=0.35, nms_threshold=0.1):
    # Run SSD network.
    h, w = img.shape[:2]
    if h < w and h < 640:
        scale = 640. / h
        h = 640
        w = int(w * scale)
    elif h >= w and w < 640:
        scale = 640. / w
        w = 640
        h = int(h * scale)
    img = Image.fromarray(np.uint8(img))
    resized_img = img.resize((w, h))
    net_shape = np.array(resized_img).shape[:2]
    rimg, rpredictions, rlocalisations, rbbox_img, e_ps = isess.run(
        [image_4d, predictions, localisations, bbox_img, end_points], feed_dict={img_input: resized_img})

    layer_shape = [e_ps['block3'].shape[1:3], e_ps['block4'].shape[1:3], e_ps['block5'].shape[1:3],
                   e_ps['block7'].shape[1:3], e_ps['block8'].shape[1:3], e_ps['block9'].shape[1:3]]

    # SSD default anchor boxes.
    ssd_anchors = g_ssd_model.ssd_anchors_all_layers(feat_shapes=layer_shape, img_shape=net_shape)

    # Get classes and bboxes from the net outputs.
    rclasses, rscores, rbboxes = np_methods.ssd_bboxes_select(
        rpredictions, rlocalisations[0], ssd_anchors,
        select_threshold=select_threshold, img_shape=net_shape, num_classes=2, decode=True)

    rbboxes = np_methods.bboxes_clip(rbbox_img, rbboxes)
    rclasses, rscores, rbboxes = np_methods.bboxes_sort(rclasses, rscores, rbboxes, top_k=1200)
    rclasses, rscores, rbboxes = np_methods.bboxes_nms(rclasses, rscores, rbboxes, nms_threshold=nms_threshold)
    # Resize bboxes to original image shape. Note: useless for Resize.WARP!
    rbboxes = np_methods.bboxes_resize(rbbox_img, rbboxes)

    return rclasses, rscores, rbboxes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('-v', '--video', type=str, default="171214_1.MP4",
                        help='the video to be processed')
    args = parser.parse_args()

    main_tracker = MainTracker(args.video)
    main_tracker.start_tracking()
